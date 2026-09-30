"""Small, asynchronous worker scheduling with OTP and Google OR-Tools CP-SAT.

Matrices seed the search. Only an actual-departure OTP replay can certify a
returned schedule as feasible under this feed; global optimality isn't claimed.
"""
import copy
import hashlib
import json
import math
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

from ortools.sat.python import cp_model


class Cancelled(Exception):
    pass


class RoutingTimeout(Exception):
    pass


def number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be between {low} and {high}.')
    return value


def validate(data, router):
    if not isinstance(data, dict):
        raise ValueError('JSON object required.')
    planning_mode=data.get('planningMode','full')
    if planning_mode not in ('full','fast','large'):
        raise ValueError('planningMode must be full, fast or large.')
    limit=100 if planning_mode=='large' else 20
    jobs = data.get('jobs')
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= limit:
        raise ValueError(f'This mode supports 1–{limit} jobs. Choose More jobs mode for 21–100 jobs; above 100 needs a stronger mode.')
    use_cache=data.get('useTravelCache',True)
    if not isinstance(use_cache,bool):raise ValueError('useTravelCache must be true or false.')
    default = number(data.get('serviceMinutes', 20), 'Default work duration', 0, 720)
    modes = data.get('modes', ['mtr', 'bus', 'ferry', 'light_rail', 'tram', 'funicular'])
    shift = data.get('shift', {})
    if not isinstance(shift, dict):
        raise ValueError('shift must be an object.')
    pool = data.get('workers')
    if pool is None:
        pool = [dict(shift, id=f'worker-{i+1}') for i in range(len(jobs))]
    if not isinstance(pool, list) or not 1 <= len(pool) <= limit:
        raise ValueError(f'workers must contain 1–{limit} available worker profiles; omit it to choose the worker count automatically.')
    def parse(value, name):
        try:
            dt = datetime.fromisoformat(value)
            dt = dt.replace(tzinfo=router.HK) if dt.tzinfo is None else dt.astimezone(router.HK)
        except (TypeError, ValueError):
            raise ValueError(f'{name}: supply a date and time.')
        return dt
    parsed = []
    ids = set()
    for i, worker in enumerate(pool):
        if not isinstance(worker, dict):
            raise ValueError('Each worker must be an object.')
        start = parse(worker.get('start', shift.get('start')), 'Shift start')
        end = parse(worker.get('end', shift.get('end')), 'Shift end')
        if not 0 < (end-start).total_seconds() <= 16*3600:
            raise ValueError('Shifts must be positive and at most 16 hours.')
        start_location = worker.get('startLocation', shift.get('startLocation'))
        end_location = worker.get('endLocation', shift.get('endLocation'))
        for location in (start_location, end_location):
            if location is not None:
                router.validate_request(dict(origin=location, destination=location, modes=modes, preference='fastest', departure=start.isoformat()))
        router.validate_request(dict(origin=jobs[0], destination=jobs[0], modes=modes, preference='fastest', departure=start.isoformat()))
        if end >= router.service_window()[1]:
            raise ValueError('Shift end must be inside the loaded timetable dates.')
        ident = str(worker.get('id', f'worker-{i+1}'))
        if not ident or len(ident) > 100 or ident in ids:
            raise ValueError('Worker IDs must be unique and at most 100 characters.')
        ids.add(ident)
        br = worker.get('break', data.get('break'))
        pause = None
        if br is not None:
            if not isinstance(br, dict):
                raise ValueError('break must be an object or null.')
            duration = round(number(br.get('minutes'), 'Break minutes', 1, 240)*60)
            earliest, latest = parse(br.get('earliest'), 'Break earliest start'), parse(br.get('latest'), 'Break latest finish')
            if earliest < start or latest > end or (latest-earliest).total_seconds() < duration:
                raise ValueError('The full break must fit its window and the worker shift.')
            pause = dict(seconds=duration, earliest=earliest, latest=latest)
        parsed.append(dict(id=ident, start=start, end=end, pause=pause, startLocation=start_location, endLocation=end_location))
    epoch = min(w['start'] for w in parsed)
    if (max(w['end'] for w in parsed)-epoch).total_seconds() > 24*3600:
        raise ValueError('All worker shifts must fit within a 24-hour planning period.')
    ids = set()
    clean_jobs = []
    for i, job in enumerate(jobs):
        if not isinstance(job, dict):
            raise ValueError('Each job must be an object.')
        router.validate_request(dict(origin=job, destination=job, modes=modes, preference='fastest', departure=epoch.isoformat()))
        ident = str(job.get('id', f'job-{i+1}'))
        if not ident or len(ident) > 100 or ident in ids:
            raise ValueError('Job IDs must be unique and at most 100 characters.')
        ids.add(ident)
        service = round(number(job.get('serviceMinutes', default), 'Job work duration', 0, 720)*60)
        window = job.get('window')
        a, b = epoch, max(w['end'] for w in parsed)
        if window is not None:
            if not isinstance(window, dict):
                raise ValueError('Job window must be an object.')
            a, b = parse(window.get('start'), 'Job window start'), parse(window.get('end'), 'Job window end')
            if a < epoch or b > max(w['end'] for w in parsed) or (b-a).total_seconds() < service:
                raise ValueError('The whole job must fit its window within the planning day.')
        clean_jobs.append(dict(id=ident, name=str(job.get('name') or ident)[:200], lat=job['lat'], lon=job['lon'],
                               service=service, earliest=round((a-epoch).total_seconds()), latest=round((b-epoch).total_seconds())))
    points = [dict(name='Any location', lat=None, lon=None)] + clean_jobs
    def endpoint(location):
        if location is None:
            return 0
        for i, point in enumerate(points[1:], 1):
            if point['lat'] == location['lat'] and point['lon'] == location['lon']:
                return i
        points.append(dict(name=str(location.get('name') or 'Worker base')[:200],lat=location['lat'],lon=location['lon']))
        return len(points)-1
    workers = []
    for worker in parsed:
        pause = worker['pause']
        if pause:
            pause = dict(seconds=pause['seconds'], earliest=round((pause['earliest']-epoch).total_seconds()), latest=round((pause['latest']-epoch).total_seconds()))
        workers.append(dict(id=worker['id'], start=round((worker['start']-epoch).total_seconds()), end=round((worker['end']-epoch).total_seconds()), pause=pause, startIndex=endpoint(worker['startLocation']), endIndex=endpoint(worker['endLocation'])))
    return dict(jobs=clean_jobs, workers=workers, points=points,
                epoch=epoch, modes=modes, planningMode=planning_mode, useTravelCache=use_cache,
                solverSeconds=number(data.get('solverSeconds', 20 if planning_mode=='large' else 10), 'Solver seconds per round', 1, 60),
                maxRounds=int(number(data.get('maxRounds', 10 if planning_mode in ('fast','large') else 3), 'Repair rounds', 1, 10 if planning_mode in ('fast','large') else 5)),
                maxRuntimeSeconds=number(data.get('maxRuntimeSeconds',600),'Overall time budget',30,1800))


def solve(spec, matrix, emit, cancelled, fixed_routes=None):
    """Circuit per worker; optional work/travel/break intervals cannot overlap."""
    model = cp_model.CpModel()
    jobs, workers = spec['jobs'], spec['workers']
    n = len(jobs)
    horizon = max(w['end'] for w in workers)
    used, visits, arcs, clocks, breaks, ends = [], [], [], [], [], []
    travel_terms = []
    for v, worker in enumerate(workers):
        active = model.new_bool_var(f'worker_{v}')
        used.append(active)
        present = [active] + [model.new_bool_var(f'job_{v}_{j}') for j in range(1, n+1)]
        visits.append(present)
        circuit = [(i, i, p.Not()) for i, p in enumerate(present)]
        start = [None] + [model.new_int_var(0, horizon, f'start_{v}_{j}') for j in range(1,n+1)]
        depart = [model.new_int_var(worker['start'], worker['end'], f'depart_{v}_{j}') for j in range(n+1)]
        finish = model.new_int_var(worker['start'], worker['end'], f'finish_{v}')
        ends.append(finish)
        model.add(finish == worker['start']).only_enforce_if(active.Not())
        intervals = []
        for j, job in enumerate(jobs, 1):
            model.add(start[j] >= max(worker['start'], job['earliest'])).only_enforce_if(present[j])
            model.add(start[j]+job['service'] <= min(worker['end'], job['latest'])).only_enforce_if(present[j])
            model.add(depart[j] >= start[j]+job['service']).only_enforce_if(present[j])
            model.add(finish >= start[j]+job['service']).only_enforce_if(present[j])
            intervals.append(model.new_optional_fixed_size_interval_var(start[j], job['service'], present[j], f'work_{v}_{j}'))
        arc_map = {}
        for i in range(n+1):
            for j in range(n+1):
                if i == j:
                    continue
                a, b = (worker['startIndex'] if i == 0 else i), (worker['endIndex'] if j == 0 else j)
                duration = 0 if not a or not b else matrix[a][b]
                if duration is None or duration > worker['end']-worker['start']:
                    continue
                take = model.new_bool_var(f'arc_{v}_{i}_{j}')
                arc_map[i,j] = take
                circuit.append((i,j,take))
                model.add_implication(take, present[i])
                model.add_implication(take, present[j])
                model.add((start[j] if j else finish) >= depart[i]+duration).only_enforce_if(take)
                intervals.append(model.new_optional_fixed_size_interval_var(depart[i], duration, take, f'travel_{v}_{i}_{j}'))
                travel_terms.append(duration*take)
        model.add_circuit(circuit)
        model.add(sum(present[1:]) >= active)
        pause = worker['pause']
        if pause:
            b = model.new_int_var(pause['earliest'], pause['latest']-pause['seconds'], f'break_{v}')
            intervals.append(model.new_optional_fixed_size_interval_var(b,pause['seconds'],active,f'pause_{v}'))
            model.add(finish >= b+pause['seconds']).only_enforce_if(active)
            breaks.append(b)
        else:
            breaks.append(None)
        model.add_no_overlap(intervals)
        arcs.append(arc_map)
        clocks.append((start, depart))
        # Identical worker profiles are interchangeable; reduce symmetry safely.
        if v and {k:x for k,x in worker.items() if k != 'id'} == {k:x for k,x in workers[v-1].items() if k != 'id'}:
            model.add(used[v-1] >= active)
            model.add(sum(visits[v-1][1:]) >= sum(present[1:]))
    for j in range(1,n+1):
        model.add(sum(p[j] for p in visits) == 1)
    if fixed_routes is not None:
        fixed={r['workerIndex']:{(e['logicalFrom'],e['logicalTo']) for e in r['edges']} for r in fixed_routes}
        for v, arc_map in enumerate(arcs):
            model.add(used[v] == int(v in fixed))
            if not fixed.get(v,set()).issubset(arc_map):
                return dict(status='INFEASIBLE',routes=None)
            for edge, take in arc_map.items():
                model.add(take == int(edge in fixed.get(v,set())))
    # A worker dominates every possible secondary cost (travel + finish times).
    weight = (len(workers)+n+len(workers)*2)*horizon + 1
    model.minimize(weight*sum(used) + sum(travel_terms) + sum(ends[v]-w['start'] for v,w in enumerate(workers)))

    def extract(reader):
        result = []
        for v, worker in enumerate(workers):
            if not reader.value(used[v]):
                continue
            route, index = [], 0
            for _ in range(n+1):
                nxt = next(j for (i,j), var in arcs[v].items() if i == index and reader.value(var))
                a,b=worker['startIndex'] if index==0 else index,worker['endIndex'] if nxt==0 else nxt
                route.append(dict(fromIndex=a, toIndex=b, sampleSeconds=0 if not a or not b else matrix[a][b], jobIndex=nxt, logicalFrom=index, logicalTo=nxt, departure=reader.value(clocks[v][1][index]),
                                  serviceStart=reader.value(clocks[v][0][nxt]) if nxt else None))
                index = nxt
                if index == 0:
                    break
            result.append(dict(worker=worker['id'], workerIndex=v, edges=route,
                               breakStart=reader.value(breaks[v]) if breaks[v] is not None else None,
                               finish=reader.value(ends[v])))
        return result

    class Progress(cp_model.CpSolverSolutionCallback):
        def __init__(self):
            super().__init__()
            self.last = 0
        def on_solution_callback(self):
            if cancelled():
                self.stop_search()
            if time.monotonic()-self.last > .5:
                routes = extract(self)
                emit('solve', f'Candidate: {len(routes)} workers', preview=dict(kind='candidate', routes=routes))
                self.last=time.monotonic()
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = spec['solverSeconds']
    solver.parameters.num_search_workers = 4
    solver.parameters.random_seed = 42
    # Cancellation is checked even before the first solution is found.
    done = threading.Event()
    def watch():
        while not done.wait(.2):
            if cancelled():
                solver.stop_search()
                return
    monitor = threading.Thread(target=watch, daemon=True)
    monitor.start()
    try:
        status = solver.solve(model, Progress())
    finally:
        done.set()
        monitor.join()
    if cancelled():
        raise Cancelled()
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return dict(status=solver.status_name(status), routes=None)
    return dict(status=solver.status_name(status), routes=extract(solver), objective=solver.objective_value,
                bound=solver.best_objective_bound)


class TravelCache:
    def __init__(self, router, folder):
        self.router = router
        self.path = folder / 'otp-pairs.sqlite3'
        folder.mkdir(parents=True, exist_ok=True)
        manifest = router.ROOT / 'data/generated/release-manifest.json'
        # Active feed hash protects against stale timings after rebuilds. Include
        # routing adapter source too, since ranking/filter changes affect results.
        inputs = [router.ROOT/'data/generated/hk-transit-EXPERIMENTAL.gtfs.zip',
                  router.ROOT/'data/generated/otp-smoke/build-config.json',
                  Path(router.__file__), Path(__file__)]
        selection = Path(router.__file__).with_name('route_selection.py')
        if selection.exists():inputs.append(selection)
        digest = hashlib.sha256(manifest.read_bytes() if manifest.exists() else b'')
        for path in inputs:
            digest.update(str(path.stat().st_mtime_ns).encode())
            digest.update(str(path.stat().st_size).encode())
        self.version = digest.hexdigest()
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS pairs (key TEXT PRIMARY KEY, value TEXT NOT NULL)')

    def route(self, a, b, departure, modes, use_cache=True):
        return self._route(a,b,departure,modes,False,use_cache)

    def route_fast(self, a, b, departure, modes, use_cache=True):
        return self._route(a,b,departure,modes,True,use_cache)

    def _route(self, a, b, departure, modes, fast, use_cache=True):
        if a['lat'] == b['lat'] and a['lon'] == b['lon']:
            return dict(seconds=0, itinerary=None), None
        key_data=[self.version,a['lat'],a['lon'],b['lat'],b['lon'],departure.isoformat(),sorted(modes)]
        if fast:key_data.append('fast-validation-10min-6-options')
        key = hashlib.sha256(json.dumps(key_data,separators=(',',':')).encode()).hexdigest()
        if use_cache:
            with sqlite3.connect(self.path,timeout=30) as db:
                row = db.execute('SELECT value FROM pairs WHERE key=?',(key,)).fetchone()
            if row:
                return json.loads(row[0]), True
        response = self.router.plan(dict(origin=a,destination=b,departure=departure.isoformat(),modes=modes,preference='fastest'), for_optimization='fast' if fast else True)
        options = [it for it in response['itineraries'] if not it.get('hasUnverifiedAccess')]
        if not options:
            # Routing errors are not the same as a genuinely disconnected pair.
            serious = [e for e in response.get('errors',[]) if e.get('code') not in ('NO_TRANSIT_CONNECTION','NO_TRANSIT_CONNECTION_IN_SEARCH_WINDOW')]
            if serious:
                raise RuntimeError('OTP could not route this pair: '+json.dumps(serious))
            return dict(seconds=None,itinerary=None), False
        best = min(options,key=lambda it:self.router.timestamp(it['end']))
        seconds = max(0,math.ceil(self.router.timestamp(best['end'])-departure.timestamp()))
        value = dict(seconds=seconds,itinerary=best)
        if use_cache:
            with sqlite3.connect(self.path,timeout=30) as db:
                db.execute('INSERT OR REPLACE INTO pairs VALUES (?,?)',(key,json.dumps(value)))
        return value, False


def audit_schedules(spec, schedules):
    """Independent final check; never expose an overlapping/partial plan as valid."""
    expected={j['id']:j for j in spec['jobs']}
    workers={w['id']:w for w in spec['workers']}
    seen=[]
    for schedule in schedules:
        worker=workers[schedule['worker']]
        previous=worker['start']
        pause_count=0
        for event in schedule['events']:
            start=round((datetime.fromisoformat(event['start'])-spec['epoch']).total_seconds())
            end=round((datetime.fromisoformat(event['end'])-spec['epoch']).total_seconds())
            if start<previous or end<start or end>worker['end']:
                raise RuntimeError('Schedule verification failed: overlapping events or shift overrun.')
            previous=end
            if event['kind']=='job':
                job=expected[event['jobId']]
                if start<job['earliest'] or end>job['latest'] or end-start!=job['service']:
                    raise RuntimeError('Schedule verification failed: job duration/window.')
                seen.append(job['id'])
            if event['kind']=='break':
                pause_count+=1
                pause=worker['pause']
                if not pause or start<pause['earliest'] or end>pause['latest'] or end-start!=pause['seconds']:
                    raise RuntimeError('Schedule verification failed: break window.')
        if pause_count!=int(worker['pause'] is not None):
            raise RuntimeError('Schedule verification failed: missing/extra break.')
    if sorted(seen)!=sorted(expected):
        raise RuntimeError('Schedule verification failed: missing/duplicate jobs.')


def run(spec, cache, emit, cancelled, seed_provider=None):
    if spec.get('planningMode')=='large':
        from large_solver import solve as search
    else:
        search=solve
    started = time.monotonic()
    points = spec['points']
    n = len(points)
    matrix = [[0 if i==j else None for j in range(n)] for i in range(n)]
    metrics = dict(useTravelCache=spec.get('useTravelCache',True),cacheHits=0,otpRequests=0,matrixSeconds=0,solveSeconds=0,validationSeconds=0,rounds=0)
    def iso(offset):
        return (spec['epoch']+timedelta(seconds=offset)).isoformat()
    def count(hit):
        if hit is not None:metrics['cacheHits' if hit else 'otpRequests'] += 1
    def check():
        if cancelled():
            raise Cancelled()
    def lookup(i,j,departure):
        try:
            route=getattr(cache,'route_fast',cache.route) if spec.get('planningMode') in ('fast','large') else cache.route
            options={} if spec.get('useTravelCache',True) else {'use_cache':False}
            return route(points[i],points[j],departure,spec['modes'],**options)
        except TimeoutError as exc:
            raise RoutingTimeout(f'OTP timed out for {points[i]["name"]} → {points[j]["name"]}. No checked schedule was produced. Retry to reuse completed pairs, or use a smaller case.') from exc
    pairs={(i,j) for i in range(1,len(spec['jobs'])+1) for j in range(1,len(spec['jobs'])+1) if i!=j}
    for worker in spec['workers']:
        for j in range(1,len(spec['jobs'])+1):
            if worker['startIndex'] and worker['startIndex'] != j:
                pairs.add((worker['startIndex'],j))
            if worker['endIndex'] and worker['endIndex'] != j:
                pairs.add((j,worker['endIndex']))
    matrix_start=time.monotonic()
    if spec.get('planningMode') in ('fast','large'):
        if seed_provider is None:raise RuntimeError('Fast mode needs the R5 matrix provider.')
        emit('matrix','R5 travel-time matrix: preparing all destinations together.',done=0,total=len(points)-1,points=points)
        rows,extra=seed_provider(spec,emit,cancelled)
        check()
        metrics.update(extra)
        for row in rows:
            i,j=row['fromIndex'],row['toIndex']
            if i==j:continue
            seconds=row['seconds']
            if seconds is not None and (not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or seconds<0):
                raise RuntimeError('Invalid R5 travel-time matrix value.')
            matrix[i][j]=seconds
        emit('matrix',('R5 matrix ready: 10-minute samples; wider retries only for origins with no connections. Missing pairs remain unavailable search arcs. Selected journeys will be checked with OTP.' if spec.get('planningMode')=='large' else 'R5 matrix ready. 90th-percentile travel times over 10 minutes (60-minute retry for missing connections); selected journeys will be checked with OTP.'),matrixRows=rows)
    else:
        emit('matrix','Calculating directed OTP journeys',done=0,total=len(pairs),points=points)
        matrix_start=time.monotonic()
        def pair(i,j):
            check()
            value,hit=lookup(i,j,spec['epoch'])
            return i,j,value,hit
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(pair,i,j) for i,j in sorted(pairs)]
            try:
                for done,future in enumerate(as_completed(futures),1):
                    check()
                    i,j,value,hit=future.result()
                    count(hit)
                    matrix[i][j]=value['seconds']
                    # Only send geometry/mode for intermediate previews, not megabytes
                    # of provenance at every progress update.
                    legs = (value['itinerary'] or {}).get('legs',[])
                    preview = dict(kind='pair',fromIndex=i,toIndex=j,seconds=value['seconds'],
                                   legs=[{k:l[k] for k in ('mode','legGeometry','apiPath') if k in l} for l in legs])
                    emit('matrix',f'{points[i]["name"]} → {points[j]["name"]}',done=done,total=len(futures),preview=preview)
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
    metrics['matrixSeconds']=round(time.monotonic()-matrix_start,3)
    emit('matrix','Travel-time matrix ready',done=len(pairs),total=len(pairs),metrics=metrics.copy())
    last_issues=[]
    fixed_routes=None
    for round_index in range(spec['maxRounds']):
        check()
        metrics['rounds']=round_index+1
        emit('solve',f'Assigning jobs and ordering visits · round {round_index+1}',round=round_index+1)
        stamp=time.monotonic()
        answer=search(spec,matrix,emit,cancelled,fixed_routes)
        if fixed_routes is not None and not answer['routes']:
            emit('repair','The current assignment no longer fits; searching new assignments.')
            # Try a compact interchangeable pool before expanding to every
            # available worker. This avoids rebuilding a 20-worker search when
            # a checked four-worker assignment needs a small repair.
            homogeneous=all({k:v for k,v in w.items() if k!='id'}=={k:v for k,v in spec['workers'][0].items() if k!='id'} for w in spec['workers'])
            if spec.get('planningMode')=='fast' and homogeneous:
                compact=dict(spec,workers=spec['workers'][:min(len(spec['workers']),len(fixed_routes)+1)])
                answer=search(compact,matrix,emit,cancelled)
                if not answer['routes']:answer=search(spec,matrix,emit,cancelled)
            else:
                answer=search(spec,matrix,emit,cancelled)
        metrics['solveSeconds']+=round(time.monotonic()-stamp,3)
        if not answer['routes']:
            return dict(feasible=False,status='no_validated_solution',solverStatus=answer['status'],
                        message='No schedule found within this search budget and sampled travel times. This does not prove the real transit problem is infeasible.',
                        issues=last_issues,recommendation='increase_budget_or_use_stronger_mode',metrics=dict(metrics,totalSeconds=round(time.monotonic()-started,3)))
        emit('validate','Checking the candidate at actual departure times',preview=dict(kind='candidate',routes=answer['routes']),metrics=metrics.copy())
        stamp=time.monotonic()
        schedules,issues=[],[]
        prefetched={}
        if spec.get('planningMode') in ('fast','large'):
            selected={(e['fromIndex'],e['toIndex'],e['departure']) for r in answer['routes'] for e in r['edges'] if e['fromIndex'] and e['toIndex']}
            with ThreadPoolExecutor(max_workers=2) as pool:
                def checked_pair(key):
                    check()
                    i,j,departure=key
                    return lookup(i,j,spec['epoch']+timedelta(seconds=departure))
                futures={pool.submit(checked_pair,key):key for key in selected}
                try:
                    for done,future in enumerate(as_completed(futures),1):
                        check()
                        key=futures[future];prefetched[key]=future.result()
                        count(prefetched[key][1])
                        emit('validate',f'OTP checked selected journey {done} of {len(selected)}.',done=done,total=len(selected),metrics=dict(metrics,validationSeconds=metrics['validationSeconds']+round(time.monotonic()-stamp,3)))
                except BaseException:
                    for future in futures:future.cancel()
                    raise
        checked=0
        total=sum(len(r['edges']) for r in answer['routes'])
        for route in answer['routes']:
            worker=spec['workers'][route['workerIndex']]
            events=[]
            travel_seconds=0
            for edge in route['edges']:
                check()
                i,j=edge['fromIndex'],edge['toIndex']
                departure=edge['departure']
                open_end=not i or not j
                if open_end:
                    value,hit=dict(seconds=0,itinerary=None),True
                else:
                    value,hit=prefetched[(i,j,departure)] if spec.get('planningMode') in ('fast','large') else lookup(i,j,spec['epoch']+timedelta(seconds=departure))
                    if spec.get('planningMode') not in ('fast','large'):count(hit)
                seconds=value['seconds']
                allowance=edge['sampleSeconds']
                if spec.get('planningMode') in ('fast','large'):
                    # A longer journey is still valid if it fits the actual
                    # appointment slack, without crossing a mandatory break.
                    allowance=(edge['serviceStart'] if edge['jobIndex'] else route['finish'])-departure
                    if worker['pause'] and departure<route['breakStart']:
                        allowance=min(allowance,route['breakStart']-departure)
                if seconds is None or seconds>allowance:
                    issues.append(dict(worker=worker['id'],fromId=points[i].get('id','anywhere'),toId=points[j].get('id','anywhere'),
                                       departure=iso(departure),previousSeconds=allowance,actualSeconds=seconds))
                    matrix[i][j]=seconds if seconds is None else max(seconds,matrix[i][j] or 0)
                if seconds is not None and not open_end:
                    travel_seconds+=seconds
                    events.append(dict(kind='travel',fromIndex=i,toIndex=j,start=iso(departure),end=iso(departure+seconds),seconds=seconds,itinerary=value['itinerary']))
                if edge['jobIndex']:
                    job=spec['jobs'][edge['jobIndex']-1]
                    events.append(dict(kind='job',jobId=job['id'],name=job['name'],point=job,start=iso(edge['serviceStart']),end=iso(edge['serviceStart']+job['service']),seconds=job['service']))
                checked+=1
                emit('validate',f'{worker["id"]}: {points[i]["name"]} → {points[j]["name"]}',done=checked,total=total)
            if worker['pause']:
                b=route['breakStart']
                events.append(dict(kind='break',start=iso(b),end=iso(b+worker['pause']['seconds']),seconds=worker['pause']['seconds']))
            events.sort(key=lambda e:(e['start'],e['end']))
            schedules.append(dict(worker=worker['id'],shiftStart=iso(worker['start']),shiftEnd=iso(worker['end']),finish=iso(route['finish']),
                                  travelSeconds=travel_seconds,jobCount=sum(e['kind']=='job' for e in events),events=events))
        metrics['validationSeconds']+=round(time.monotonic()-stamp,3)
        if not issues:
            # Explicitly show idle periods, so the time budget is fully accounted for.
            for schedule in schedules:
                expanded=[]
                cursor=schedule['shiftStart']
                for event in schedule['events']:
                    if event['start']>cursor:
                        expanded.append(dict(kind='idle',start=cursor,end=event['start'],seconds=round((datetime.fromisoformat(event['start'])-datetime.fromisoformat(cursor)).total_seconds())))
                    expanded.append(event)
                    cursor=max(cursor,event['end'])
                if cursor<schedule['finish']:
                    expanded.append(dict(kind='idle',start=cursor,end=schedule['finish'],seconds=round((datetime.fromisoformat(schedule['finish'])-datetime.fromisoformat(cursor)).total_seconds())))
                schedule['events']=expanded
            check()
            audit_schedules(spec,schedules)
            return dict(feasible=True,status='validated',planningMode=spec.get('planningMode','full'),workersUsed=len(schedules),schedules=schedules,points=points,
                        metrics=dict(metrics,totalSeconds=round(time.monotonic()-started,3)),minimumWorkersProven=False,
                        solverStatus=answer['status'],message='Every job, shift and break checked with OTP at the scheduled departure times. Fewest workers found; global minimum not proven. Waits are included. Timings remain estimates from the loaded feed.')
        last_issues=issues
        fixed_routes=answer['routes']
        emit('repair',f'{len(issues)} journeys need more time or are unavailable; updating the matrix and solving again.',issues=issues)
    return dict(feasible=False,status='needs_review',issues=last_issues,recommendation='increase_budget_or_use_stronger_mode',metrics=dict(metrics,totalSeconds=round(time.monotonic()-started,3)),
                message='No fully validated schedule within the repair limit. Increase the budget or adjust shifts/jobs; no provisional route is presented as a final result.')


class Manager:
    def __init__(self, router, folder=None):
        self.router=router
        self.folder=folder or router.ROOT/'data/optimization'
        self.folder.mkdir(parents=True,exist_ok=True)
        self.cache=TravelCache(router,self.folder)
        self.lock=threading.RLock()
        self.jobs={}
        self.active=None
        self.fast=None

    def start(self,data):
        spec=validate(data,self.router)
        with self.lock:
            if self.active:
                raise RuntimeError('An optimisation is already running. Wait or cancel it before starting another.')
            ident=uuid.uuid4().hex
            job=dict(id=ident,status='running',events=[],result=None,startedAt=time.time(),cancel=threading.Event())
            self.jobs[ident]=job
            self.active=ident
            # Bound memory retained by a long-running local test server.
            if len(self.jobs)>10:
                del self.jobs[next(iter(self.jobs))]
        def emit(stage,message,**extra):
            with self.lock:
                job['events'].append(dict(seq=len(job['events'])+1,stage=stage,message=message,elapsedSeconds=round(time.time()-job['startedAt'],2),**extra))
        def background():
            stopped=lambda: job['cancel'].is_set() or time.time()-job['startedAt'] >= spec['maxRuntimeSeconds']
            try:
                emit('validate_input',f'{len(spec["jobs"])} jobs accepted · up to {len(spec["workers"])} workers',done=1,total=1)
                if not spec['useTravelCache']:emit('validate_input','Travel-result cache off: recalculate every OTP journey and R5 matrix. Prepared routing networks remain reusable.')
                if spec['planningMode'] in ('fast','large') and self.fast is None:
                    from r5_matrix import R5Matrix
                    self.fast=R5Matrix(self.router.ROOT,self.folder)
                job['result']=run(spec,self.cache,emit,stopped,self.fast if spec['planningMode'] in ('fast','large') else None)
                job['status']='complete'
                emit('complete',job['result']['message'],metrics=job['result']['metrics'])
            except Cancelled:
                if job['cancel'].is_set():
                    job['status']='cancelled'
                    emit('cancelled','Stopped. Completed OTP journeys remain cached for the next run.')
                else:
                    job['status']='complete'
                    job['result']=dict(feasible=False,status='budget_exceeded',recommendation='increase_budget_or_use_stronger_mode',
                        message='The selected mode reached its time budget without a checked result. Increase the budget or try the other planning mode. Completed travel times remain cached.',
                        metrics=dict(totalSeconds=round(time.time()-job['startedAt'],3)))
                    emit('complete',job['result']['message'],metrics=job['result']['metrics'])
            except RoutingTimeout as exc:
                job['status']='complete'
                job['result']=dict(feasible=False,status='routing_timeout',recommendation='retry_cached_pairs_or_use_stronger_mode',
                    message=str(exc),metrics=dict(totalSeconds=round(time.time()-job['startedAt'],3)))
                emit('complete',str(exc),metrics=job['result']['metrics'])
            except Exception as exc:
                job['status']='error'
                job['error']=str(exc)
                emit('error',str(exc))
            finally:
                with self.lock:
                    job['finishedAt']=time.time()
                    self.active=None
                    saved={k:v for k,v in job.items() if k!='cancel'}
                    path=self.folder/(ident+'.json')
                    temporary=path.with_suffix('.tmp')
                    temporary.write_text(json.dumps(saved,ensure_ascii=False))
                    temporary.replace(path)
        threading.Thread(target=background,daemon=True).start()
        return dict(id=ident,status='running',statusUrl='/api/optimizations/'+ident)

    def get(self,ident,after=0):
        with self.lock:
            if ident not in self.jobs:
                if len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident):
                    raise KeyError('Unknown optimisation.')
                path=self.folder/(ident+'.json')
                if not path.exists():
                    raise KeyError('Unknown optimisation.')
                job=json.loads(path.read_text())
            else:
                job=self.jobs[ident]
            return dict(id=ident,status=job['status'],events=[e for e in job['events'] if e['seq']>after],
                        result=job['result'],error=job.get('error'),elapsedSeconds=round(job.get('finishedAt',time.time())-job['startedAt'],2))

    def cancel(self,ident):
        with self.lock:
            if ident not in self.jobs:
                raise KeyError('Unknown optimisation.')
            self.jobs[ident]['cancel'].set()
        return dict(id=ident,status='cancelling')
