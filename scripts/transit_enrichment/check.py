"""Independent protected-field checks for experimental timing enrichment."""
from collections import defaultdict
from itertools import zip_longest
from pathlib import Path
import json
import zipfile
from .feed import rows, seconds, save, sha


def verify(anchors, surface, output, report_path=None):
    changed, protected, non_surface = 0, 0, 0
    with zipfile.ZipFile(anchors) as a, zipfile.ZipFile(surface) as s, zipfile.ZipFile(output) as z:
        proof = json.loads(z.read('transit_enrichment_provenance.json'))
        changed_trips = set(proof['history'])
        for name in s.namelist():
            if name in ('stop_times.txt', 'transit_enrichment_provenance.json'): continue
            # CRC and length detect unintended table changes without loading rail tables.
            left,right=s.getinfo(name),z.getinfo(name)
            if (left.CRC,left.file_size)!=(right.CRC,right.file_size):
                raise ValueError('Timing stage changed unrelated table: '+name)
        previous = {}
        for old, base, new in zip_longest(rows(a,'stop_times.txt'), rows(s,'stop_times.txt'),rows(z,'stop_times.txt')):
            if old is None or base is None or new is None: raise ValueError('Stop visit count changed')
            for key in ('trip_id','stop_id','stop_sequence'):
                if old[key]!=new[key] or base[key]!=new[key]: raise ValueError('Stop visit order changed')
            tid=new['trip_id']
            for k,v in old.items():
                if k in ('arrival_time','departure_time') and not v: continue
                if k=='timepoint' and not old.get('arrival_time') and not old.get('departure_time'):continue
                if new.get(k)!=v:raise ValueError('Source anchor/field overwritten: '+tid+'/'+k)
                if k in ('arrival_time','departure_time') and v:protected+=1
            if tid not in changed_trips:
                if base!=new:raise ValueError('Unapproved trip timing changed: '+tid)
                non_surface+=1
            elif base!=new:
                if tid not in proof['trips']:raise ValueError('Non-surface transit changed')
                changed+=1
            if new.get('arrival_time') and new.get('departure_time'):
                arrival,departure=seconds(new['arrival_time']),seconds(new['departure_time'])
                if arrival>departure or arrival<previous.get(tid,-1):raise ValueError('Nonmonotonic stop times: '+tid)
                previous[tid]=departure
        if changed!=proof['statistics']['history_rows_reweighted']:
            raise ValueError('Reported changed timing count differs')
    result=dict(valid=True,source_timing_fields_preserved=protected,changed_intermediate_rows=changed,
                unchanged_other_rows=non_surface,anchors_sha256=sha(anchors),surface_sha256=sha(surface),output_sha256=sha(output))
    if report_path:save(report_path,result)
    return result


def verify_base(source, output, report_path=None):
    """Independently prove that timetable replacement preserves existing visits."""
    with zipfile.ZipFile(source) as a, zipfile.ZipFile(output) as z:
        proof=json.loads(z.read('transit_enrichment_provenance.json'))
        known=set(proof['timetables'])
        old_trips={t['trip_id']:t for t in rows(a,'trips.txt') if t['trip_id'] in proof['trips']}
        originals={}
        all_new=set(); retained={}
        old_iter=iter(rows(a,'trips.txt'))
        for new in rows(z,'trips.txt'):
            tid=new['trip_id']
            if tid in all_new:raise ValueError('Duplicate trip ID after timetable merge')
            all_new.add(tid)
            if tid in known:
                original=old_trips[proof['timetables'][tid]['original_trip']]
                if any(v!=new[k] for k,v in original.items() if k not in ('trip_id','service_id')):
                    raise ValueError('Official timetable changed route/direction/headsign')
                originals[tid]=original['trip_id'];continue
            old=next(old_iter,None)
            if old is None or old['trip_id']!=tid:raise ValueError('Original trip order/count changed')
            if new!=old:
                if not new['service_id'].startswith('ENR:RETAIN:') or dict(new,service_id=old['service_id'])!=old:
                    raise ValueError('Unrelated trip fields changed')
                pattern=proof['identities']['patterns'][proof['trips'][tid]]
                if len(pattern['matches'])!=1 or pattern['matches'][0]['operator']!='GMB':
                    raise ValueError('Changed non-GMB service')
                retained[new['service_id']]=old['service_id']
        if next(old_iter,None) is not None:raise ValueError('Missing original trip')
        if not known <= all_new:raise ValueError('Missing generated timetable trips')
        original_times=defaultdict(list)
        wanted=set(originals.values())
        reader=iter(rows(z,'stop_times.txt'));count=0
        for old in rows(a,'stop_times.txt'):
            if old!=next(reader,None):raise ValueError('Original stop timing/field changed by timetable merge')
            if old['trip_id'] in wanted:original_times[old['trip_id']].append(old)
            count+=1
        generated=defaultdict(list)
        for row in reader:
            if row['trip_id'] not in known:raise ValueError('Unapproved additional stop time')
            generated[row['trip_id']].append(row)
        for tid,original_id in originals.items():
            old,new=original_times[original_id],generated[tid]
            if len(old)!=len(new):raise ValueError('Official timetable changed stop count')
            offset=seconds(new[0]['departure_time'])-seconds(old[0]['departure_time'])
            for left,right in zip(old,new):
                for k,v in left.items():
                    if k=='trip_id':continue
                    if k in ('arrival_time','departure_time') and v:
                        if seconds(right[k])!=seconds(v)+offset:raise ValueError('Official timetable altered running offsets')
                    elif right[k]!=v:raise ValueError('Official timetable altered visit metadata')
        # Original calendars/exceptions/frequencies must remain an exact prefix.
        for name in ('calendar.txt','calendar_dates.txt','frequencies.txt'):
            it=iter(rows(z,name))
            for row in rows(a,name):
                if row!=next(it,None):raise ValueError('Original '+name+' changed')
        dates=list(rows(z,'calendar_dates.txt'))
        by_service=defaultdict(dict)
        for r in dates:
            key=(r['service_id'],r['date'])
            if r['date'] in by_service[r['service_id']]:raise ValueError('Duplicate service-day exception')
            by_service[r['service_id']][r['date']]=r['exception_type']
        from datetime import date,timedelta
        start,end=map(date.fromisoformat,proof['window'])
        days=[(start+timedelta(days=i)).strftime('%Y%m%d') for i in range((end-start).days+1)]
        for sid,old_sid in retained.items():
            if any(by_service[sid].get(d)!='2' for d in days):raise ValueError('Old schedule not fully excluded inside window')
            expected={d:v for d,v in by_service[old_sid].items() if d not in days}
            actual={d:v for d,v in by_service[sid].items() if d not in days}
            if actual!=expected:raise ValueError('Old exceptions lost outside window')
        result=dict(valid=True,original_stop_visits_preserved=count,new_timetable_trips=len(known),
                    source_sha256=sha(source),output_sha256=sha(output))
    if report_path:save(report_path,result)
    return result
