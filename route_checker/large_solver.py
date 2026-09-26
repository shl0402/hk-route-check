"""Routing search for up to 100 jobs, followed by exact fixed-order scheduling.

The RoutingModel proposes orders. CP-SAT places non-interruptible travel, work
and breaks. Neither sampled times nor a routing incumbent certify a final plan;
optimizer.run always checks selected departures with OTP afterwards.
"""
import threading
import time
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from ortools.sat.python import cp_model


def retime(spec, matrix, orders, cancelled, seconds):
    model = cp_model.CpModel()
    records, objective = [], []
    horizon = max(w['end'] for w in spec['workers'])
    for v, order in orders:
        w = spec['workers'][v]
        intervals, edges, previous = [], [], w['start']
        finish = model.new_int_var(w['start'], w['end'], f'finish{v}')
        for k, (a, b) in enumerate(zip([0]+order, order+[0])):
            i, j = (a or w['startIndex']), (b or w['endIndex'])
            duration = 0 if not i or not j else matrix[i][j]
            if duration is None:
                return None
            duration = int(duration)
            depart = model.new_int_var(w['start'], w['end'], f'd{v}_{k}')
            model.add(depart >= previous)
            model.add(depart + duration <= w['end'])
            intervals.append(model.new_fixed_size_interval_var(depart, duration, f't{v}_{k}'))
            start = None
            if b:
                job = spec['jobs'][b-1]
                low, high = max(w['start'], job['earliest']), min(w['end'], job['latest'])-job['service']
                if high < low:
                    return None
                start = model.new_int_var(low, high, f's{v}_{k}')
                model.add(start >= depart + duration)
                intervals.append(model.new_fixed_size_interval_var(start, job['service'], f'j{v}_{k}'))
                previous = start + job['service']
            else:
                model.add(finish >= depart + duration)
            edges.append((i, j, a, b, duration, depart, start))
            objective.append(depart)
        pause = w['pause']
        br = None
        if pause:
            br = model.new_int_var(pause['earliest'], pause['latest']-pause['seconds'], f'b{v}')
            intervals.append(model.new_fixed_size_interval_var(br, pause['seconds'], f'break{v}'))
            model.add(finish >= br + pause['seconds'])
        model.add_no_overlap(intervals)
        objective.append(finish)
        records.append((v, edges, br, finish))
    model.minimize(sum(objective))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(.01, seconds)
    solver.parameters.num_search_workers = 4
    solver.parameters.random_seed = 42
    done = threading.Event()
    def watch():
        while not done.wait(.1):
            if cancelled():
                solver.stop_search()
                return
    monitor = threading.Thread(target=watch, daemon=True)
    monitor.start()
    try:
        status = solver.solve(model)
    finally:
        done.set()
        monitor.join()
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    routes = []
    for v, edges, br, finish in records:
        routes.append(dict(worker=spec['workers'][v]['id'], workerIndex=v,
            edges=[dict(fromIndex=i,toIndex=j,logicalFrom=a,logicalTo=b,
                        jobIndex=b,sampleSeconds=duration,departure=solver.value(depart),
                        serviceStart=solver.value(start) if start is not None else None)
                   for i,j,a,b,duration,depart,start in edges],
            breakStart=solver.value(br) if br is not None else None,finish=solver.value(finish)))
    return routes



def repartition(spec, matrix, orders, cancelled, deadline):
    """Keep visit order while splitting at feasible boundaries after a repair.

    Only interchangeable profiles may be reused this way. This greedy repair
    does not claim a minimum; all returned intervals still pass exact retiming.
    """
    workers=spec['workers']
    profile=lambda w:{k:v for k,v in w.items() if k!='id'}
    if not all(profile(w)==profile(workers[0]) for w in workers):return None
    pending=[job for _,order in orders for job in order]
    result=[]
    for v in range(len(workers)):
        if not pending:return result
        low,high,best=1,len(pending),None
        while low<=high and time.monotonic()<deadline and not cancelled():
            # With a fixed end base, adding a job can shorten the final ride;
            # feasibility is not monotone. Scan longest first in that case.
            middle=high if workers[v]['endIndex'] else (low+high)//2
            trial=retime(spec,matrix,[(v,pending[:middle])],cancelled,min(.3,max(.01,deadline-time.monotonic())))
            if trial:
                best=(middle,trial[0]);low=middle+1
                if workers[v]['endIndex']:break
            else:high=middle-1
        if best is None:return None
        length,route=best
        result.append(route);pending=pending[length:]
    return result if not pending else None


def solve(spec, matrix, emit, cancelled, fixed_routes=None):
    from optimizer import Cancelled
    deadline = time.monotonic() + spec['solverSeconds']
    def check():
        if cancelled():
            raise Cancelled()
    check()
    if fixed_routes is not None:
        orders = [(r['workerIndex'], [e['jobIndex'] for e in r['edges'] if e['jobIndex']]) for r in fixed_routes]
        routes = retime(spec, matrix, orders, cancelled, spec['solverSeconds']*.5)
        if not routes:
            emit('repair','Rebalancing existing visit orders to fit the longer checked journeys.')
            routes=repartition(spec,matrix,orders,cancelled,deadline)
        check()
        return dict(status='FIXED_ORDER_FEASIBLE' if routes else 'FIXED_ORDER_NO_SOLUTION', routes=routes)
    n, workers = len(spec['jobs']), spec['workers']
    horizon = max(w['end'] for w in workers)
    count = len(workers)
    # Dedicated terminals permit different bases, including open starts/ends.
    physical = list(range(1,n+1)) + [w['startIndex'] for w in workers] + [w['endIndex'] for w in workers]
    manager = pywrapcp.RoutingIndexManager(n+2*count,count,list(range(n,n+count)),list(range(n+count,n+2*count)))
    routing = pywrapcp.RoutingModel(manager)
    def transit(a,b):
        a,b = manager.IndexToNode(a), manager.IndexToNode(b)
        if a >= n and b >= n: # unused vehicle
            return 0
        i,j = physical[a],physical[b]
        travel = 0 if not i or not j else matrix[i][j]
        return (horizon+1 if travel is None else int(travel)) + (spec['jobs'][a]['service'] if a<n else 0)
    callback = routing.RegisterTransitCallback(transit)
    routing.SetArcCostEvaluatorOfAllVehicles(callback)
    # One extra worker costs more than every possible travel/service arc combined.
    routing.SetFixedCostOfAllVehicles((n+count)*horizon+1)
    routing.AddDimension(callback,horizon,horizon,False,'Time')
    clocks = routing.GetDimensionOrDie('Time')
    capacities = [w['end']-w['start']-(w['pause']['seconds'] if w['pause'] else 0) for w in workers]
    routing.AddDimensionWithVehicleCapacity(callback,0,capacities,True,'Workload')
    for i,job in enumerate(spec['jobs']):
        if job['latest']-job['service'] < job['earliest']:
            return dict(status='INVALID_WINDOW',routes=None)
        clocks.CumulVar(manager.NodeToIndex(i)).SetRange(job['earliest'],job['latest']-job['service'])
    for v,w in enumerate(workers):
        clocks.CumulVar(routing.Start(v)).SetRange(w['start'],w['end'])
        clocks.CumulVar(routing.End(v)).SetRange(w['start'],w['end'])
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    # CP-SAT needs a small part of the same round budget to place breaks exactly.
    params.time_limit.FromMilliseconds(max(50,int(spec['solverSeconds']*.75*1000)))
    best = None
    last = 0
    def candidate():
        nonlocal best,last
        if cancelled():
            routing.solver().FinishCurrentSearch()
            return
        value = routing.CostVar().Value()
        if best is not None and value >= best:
            return
        best=value
        if time.monotonic()-last>.5:
            used=sum(routing.NextVar(routing.Start(v)).Value()!=routing.End(v) for v in range(count))
            emit('solve',f'Routing search: candidate with {used} workers; checking breaks next.')
            last=time.monotonic()
    routing.AddAtSolutionCallback(candidate)
    done=threading.Event()
    def watch():
        while not done.wait(.1):
            if cancelled():
                routing.solver().FinishCurrentSearch()
                return
    monitor=threading.Thread(target=watch,daemon=True)
    monitor.start()
    try:
        assignment = routing.SolveWithParameters(params)
    finally:
        done.set();monitor.join()
    check()
    if assignment is None:
        return dict(status='ROUTING_NO_SOLUTION',routes=None)
    orders=[]
    for v in range(count):
        index=assignment.Value(routing.NextVar(routing.Start(v)))
        order=[]
        while not routing.IsEnd(index):
            order.append(manager.IndexToNode(index)+1)
            index=assignment.Value(routing.NextVar(index))
        if order:orders.append((v,order))
    routes=retime(spec,matrix,orders,cancelled,max(.05,(deadline-time.monotonic())*.5))
    if not routes:
        emit('solve','Splitting visit orders where needed to fit uninterrupted breaks.')
        routes=repartition(spec,matrix,orders,cancelled,deadline)
    check()
    if routes:
        emit('solve',f'{len(routes)} worker routes fit shifts and breaks; checking journeys next.',preview=dict(kind='candidate',routes=routes))
    return dict(status='ROUTING_FEASIBLE' if routes else 'BREAK_SCHEDULING_NO_SOLUTION',routes=routes)
