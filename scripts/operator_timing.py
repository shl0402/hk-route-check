#!/usr/bin/env python3
"""Apply verified Sun Ferry passenger timetables; preserve all other services.

Only operator-published ranges are used. Their upper bound is a planning
allowance, not a measured or guaranteed arrival. No district-level KMB
prediction is converted into an invented stop-pair time.
"""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import io
import json
from pathlib import Path
import zipfile
import operator_sources as sources

ROUTES={'cheungchau':('7005','101101','Cheung Chau'), 'muiwo':('7006','101107','Mui Wo')}
DAYS=['monday','tuesday','wednesday','thursday','friday','saturday','sunday']

def seconds(value):
    h,m,s=map(int,value.split(':'));return h*3600+m*60+s

def clock(value):return f'{value//3600:02d}:{value//60%60:02d}:{value%60:02d}'

def rows(z,name):return csv.DictReader(io.TextIOWrapper(z.open(name),encoding='utf-8-sig'))

def write_rows(z,name,fields,values):
    with z.open(name,'w') as f:
        text=io.TextIOWrapper(f,encoding='utf-8',newline='')
        writer=csv.DictWriter(text,fieldnames=fields,lineterminator='\n')
        writer.writeheader();writer.writerows(values);text.flush();text.detach()

def service_map(calendars,exceptions,trip_services):
    """Reuse TD weekday/holiday calendars, checking their actual exceptions."""
    candidates=[c for c in calendars if c['service_id'] in trip_services]
    by_days=defaultdict(list)
    for c in candidates:
        days=tuple(i for i,d in enumerate(DAYS) if c[d]=='1')
        by_days[days].append(c['service_id'])
    result={}
    for days in [(0,1,2,3,4),(5,),(0,1,2,3,4,5),(6,)]:
        ids=by_days[days]
        if len(ids)!=1:raise ValueError('Ferry service calendar is not uniquely identified: '+str(days))
        result[days]=ids[0]
    holiday={e['date'] for e in exceptions if e['service_id']==result[(6,)] and e['exception_type']=='1'}
    if not holiday:raise ValueError('Missing published holiday exceptions')
    for days,sid in result.items():
        if days==(6,):continue
        removed={e['date'] for e in exceptions if e['service_id']==sid and e['exception_type']=='2'}
        required={d for d in holiday if datetime.strptime(d,'%Y%m%d').weekday() in days}
        if not required<=removed:raise ValueError('Ferry calendar does not exclude public holidays')
    result[(6,7)]=result[(6,)]
    return result

def merge(root,base=None,output=None):
    data=sources.normalize(root)
    base=base or root/'data/generated/hk-transit-SURFACE.gtfs.zip'
    output=output or root/'data/generated/hk-transit-OPERATOR.gtfs.zip'
    proof=dict(format=1,source_manifest=data['source_manifest'],trips={},
        policy='Upper end of the operator-published journey range. Departure times, day rules and vessel classes come from the current passenger timetable. TD service-day/holiday calendars and existing overnight offsets are preserved.',
        kmb_section_verification=data['section_verification'])
    with zipfile.ZipFile(base) as old:
        trips=list(rows(old,'trips.txt'));target_ids={v[0] for v in ROUTES.values()}
        removed={t['trip_id']:t for t in trips if t['route_id'] in target_ids}
        if not removed:raise ValueError('No verified ferry route identities in feed')
        stops={s['stop_id']:s for s in rows(old,'stops.txt')}
        for sid,expected in [('101102','Central Pier No. 5'),('101108','Central Pier No. 6'),('101101','Cheung Chau'),('101107','Mui Wo')]:
            if expected not in stops.get(sid,{}).get('stop_name',''):raise ValueError('TD pier identity changed: '+sid)
        stop_fields=rows(old,'stop_times.txt').fieldnames
        original=defaultdict(list)
        for r in rows(old,'stop_times.txt'):
            if r['trip_id'] in removed:original[r['trip_id']].append(r)
        services=service_map(list(rows(old,'calendar.txt')),list(rows(old,'calendar_dates.txt')),{t['service_id'] for t in removed.values()})
        overnight={}
        for tid,rs in original.items():
            if len(rs)!=2:raise ValueError('Ferry trip no longer has exactly two stops: '+tid)
            rs.sort(key=lambda r:int(r['stop_sequence']));t=removed[tid]
            direction=0 if rs[0]['stop_id'] in ('101102','101108') else 1
            sec=seconds(rs[0]['departure_time'])
            overnight[(t['route_id'],direction,t['service_id'],sec%86400)]=sec
        if any(r['trip_id'] in removed for r in rows(old,'frequencies.txt')):
            raise ValueError('Cannot replace a frequency-based ferry with listed departures')
        added=[];times=[]
        for kind,(rid,island_id,island_name) in ROUTES.items():
            source=data['ferries'][kind]
            for r in source['rows']:
                direction=0 if r['direction'].startswith('Central to ') else 1
                sid=services[tuple(r['days'])];dep=r['departure_seconds']
                key=(rid,direction,sid,dep)
                if dep<4*3600 and key not in overnight:raise ValueError('Unverified overnight service-day offset: '+str(key))
                dep=overnight.get(key,dep)
                pier='101102' if r['central_pier']==5 else '101108'
                pair=(pier,island_id) if direction==0 else (island_id,pier)
                tid=f'OPERATOR:SF:{kind}:{direction}:{sid}:{dep}'
                trip={k:'' for k in trips[0]}
                trip.update(route_id=rid,service_id=sid,trip_id=tid,trip_headsign=(island_name if direction==0 else 'Central')+' · '+r['vessel'].title()+' ferry',direction_id=str(direction))
                added.append(trip)
                duration=max(r['duration_range_seconds'])
                for i,stop in enumerate(pair):
                    row={k:'' for k in stop_fields}
                    row.update(trip_id=tid,stop_id=stop,stop_sequence=str(i+1),
                        arrival_time=clock(dep+i*duration),departure_time=clock(dep+i*duration),
                        pickup_type=str(i),drop_off_type=str(1-i),timepoint=str(1-i))
                    times.append(row)
                warnings=[]
                if kind=='cheungchau' and r['days']==[0,1,2,3,4,5] and r['departure_seconds'] in (81000,84600):
                    warnings.append('Temporary fast-vessel deployment may revert without notice. The published ordinary-ferry range is retained for planning.')
                proof['trips'][tid]=dict(route_id=rid,stop_ids=list(pair),departure=clock(dep),
                    duration_seconds=duration,duration_range_seconds=r['duration_range_seconds'],vessel=r['vessel'],
                    source_url=source['source']['url'],snapshot=source['source']['retrieved_at'][:10],
                    effective_date=source['effective_date'],raw=r['raw'],warnings=warnings,
                    deployment_notice_url=sources.NOTICE if warnings else None,
                    calendar_service_id=sid,source_sha256=source['source']['sha256'])
        output.parent.mkdir(parents=True,exist_ok=True);temp=output.with_suffix('.part')
        with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED) as new:
            for name in old.namelist():
                if name not in ('trips.txt','stop_times.txt','operator_timing_provenance.json'):
                    new.writestr(name,old.read(name))
            write_rows(new,'trips.txt',trips[0].keys(),[t for t in trips if t['trip_id'] not in removed]+added)
            def stop_rows():
                for row in rows(old,'stop_times.txt'):
                    if row['trip_id'] not in removed:yield row
                yield from times
            write_rows(new,'stop_times.txt',stop_fields,stop_rows())
            new.writestr('operator_timing_provenance.json',json.dumps(proof,ensure_ascii=False))
        check(base,temp)
        temp.replace(output)
    report=dict(removed_trip_templates=len(removed),published_trip_templates=len(added),
        vessel_counts=dict(Counter(p['vessel'] for p in proof['trips'].values())),
        conditional_deployment_trips=sum(bool(p['warnings']) for p in proof['trips'].values()),
        ferry_csv_comparison={k:v['csv_comparison'] for k,v in data['ferries'].items()},
        kmb=data['section_verification'],feed_sha256=sources.sha(output),base_sha256=sources.sha(base))
    sources.save(root/'data/operators/report.json',report);print(json.dumps(report,indent=2))
    return report

def check(base,candidate):
    with zipfile.ZipFile(base) as old,zipfile.ZipFile(candidate) as new:
        for name in old.namelist():
            if name not in ('trips.txt','stop_times.txt','operator_timing_provenance.json'):
                if old.read(name)!=new.read(name):raise ValueError('Changed unrelated table: '+name)
        proof=json.loads(new.read('operator_timing_provenance.json'))['trips']
        target_ids={v[0] for v in ROUTES.values()}
        oldtrips={t['trip_id']:t for t in rows(old,'trips.txt') if t['route_id'] not in target_ids}
        newtrips={t['trip_id']:t for t in rows(new,'trips.txt') if t['trip_id'] not in proof}
        if oldtrips!=newtrips:raise ValueError('Changed unrelated trips')
        before=(r for r in rows(old,'stop_times.txt') if r['trip_id'] in oldtrips)
        after=(r for r in rows(new,'stop_times.txt') if r['trip_id'] in oldtrips)
        from itertools import zip_longest
        if any(a!=b for a,b in zip_longest(before,after)):raise ValueError('Changed unrelated stop times')
        seen=Counter()
        for r in rows(new,'stop_times.txt'):
            if r['trip_id'] not in proof:continue
            p=proof[r['trip_id']];i=int(r['stop_sequence'])-1
            expected=seconds(p['departure'])+i*p['duration_seconds']
            if i not in (0,1) or r['stop_id']!=p['stop_ids'][i] or seconds(r['arrival_time'])!=expected or r['departure_time']!=r['arrival_time']:
                raise ValueError('Ferry stop time does not match operator proof')
            if r['timepoint']!=str(1-i):raise ValueError('Arrival estimate labelled exact')
            seen[r['trip_id']]+=1
        if set(seen)!=set(proof) or any(n!=2 for n in seen.values()):raise ValueError('Missing ferry stop times')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--base',type=Path);p.add_argument('--output',type=Path)
    a=p.parse_args();merge(a.root,a.base,a.output)
