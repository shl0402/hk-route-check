"""R5 lives in its own process so cancellation can interrupt native routing."""
import csv
import io
import json
import os
from pathlib import Path
import sys
import time
import traceback
import threading
from concurrent.futures import ThreadPoolExecutor
import copy
import zipfile


_output_lock=threading.Lock()

def send(kind,**fields):
    with _output_lock:print('R5JSON '+json.dumps(dict(kind=kind,**fields),allow_nan=False),flush=True)


def group(route):
    kind=str(route['route_type'])
    if kind=='0':return 'light_rail' if route['agency_id']=='RAIL:MTR' else 'tram'
    return {'1':'mtr','2':'mtr','3':'bus','4':'ferry','7':'funicular'}.get(kind)


def filtered_feed(source, target, modes):
    """Filter transport choices only. Do not change timing, trips or calendars."""
    if target.exists():return target
    with zipfile.ZipFile(source) as z:
        def rows(name):return csv.DictReader(io.TextIOWrapper(z.open(name),encoding='utf-8-sig'))
        routes={r['route_id'] for r in rows('routes.txt') if group(r) in modes}
        trips={r['trip_id'] for r in rows('trips.txt') if r['route_id'] in routes}
        filters={'routes.txt':lambda r:r['route_id'] in routes,'trips.txt':lambda r:r['trip_id'] in trips,
                 'stop_times.txt':lambda r:r['trip_id'] in trips,'frequencies.txt':lambda r:r['trip_id'] in trips}
        temporary=target.with_suffix('.tmp')
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as out:
            for name in ['agency.txt','routes.txt','stops.txt','trips.txt','stop_times.txt','calendar.txt','calendar_dates.txt','frequencies.txt','transfers.txt']:
                if name not in z.namelist():continue
                if name not in filters:out.writestr(name,z.read(name));continue
                reader=rows(name)
                with out.open(name,'w') as raw:
                    text=io.TextIOWrapper(raw,encoding='utf-8',newline='');writer=csv.DictWriter(text,fieldnames=reader.fieldnames);writer.writeheader()
                    for row in reader:
                        if filters[name](row):writer.writerow(row)
                    text.flush();text.detach()
        temporary.replace(target)
    return target


def main():
    folder=Path(sys.argv[1]);folder.mkdir(parents=True,exist_ok=True)
    os.environ['XDG_CACHE_HOME']=str(folder/'cache')
    # Keep Java from claiming the package default of 80% of system RAM.
    sys.argv=[sys.argv[0],'--max-memory','4G']
    send('progress',message='Starting R5; its engine is downloaded once if missing.')
    import r5py
    import geopandas as gpd
    from shapely.geometry import Point
    from datetime import datetime,timedelta
    import math
    network=None;version=None
    for line in sys.stdin:
        try:
            request=json.loads(line);stamp=time.monotonic();root=Path(request['root'])
            if version!=request['version']:
                send('progress',message='Loading the R5 street and transit network.')
                feed=root/'data/generated/hk-transit-EXPERIMENTAL.gtfs.zip'
                modes=request['modes']
                if set(modes)!=set(['mtr','bus','ferry','light_rail','tram','funicular']):
                    feed=filtered_feed(feed,folder/(request['version']+'.gtfs.zip'),modes) if modes else None
                network=r5py.TransportNetwork(root/'data/raw/2026-09-17/osm/hong-kong-latest.osm.pbf',[feed] if feed else [])
                version=request['version']
            build=time.monotonic()-stamp;stamp=time.monotonic()
            points=request['points'];frame=gpd.GeoDataFrame({'id':[p['id'] for p in points]},geometry=[Point(p['lon'],p['lat']) for p in points],crs=4326)
            total=len(points);done=0;windows={};progress_lock=threading.Lock()
            # This pinned extension retains R5's shared destination linkage while
            # reporting an origin at a time, rather than 380 detailed OTP calls.
            class ProgressMatrix(r5py.TravelTimeMatrix):
                def _compute(self):
                    import pandas as pd
                    self._prepare_origins_destinations()
                    self.request.destinations=self.destinations
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        frames=list(pool.map(self._travel_times_per_origin,list(self.origins.id)))
                    return pd.concat(frames,ignore_index=True)
                def _travel_times_per_origin(self,from_id):
                    import jpype
                    nonlocal done
                    task=copy.copy(self.request)
                    task.origin=self.origins[self.origins.id==from_id].geometry.item()
                    # Matrix is a search seed; final timings always come from OTP.
                    task._regional_task.monteCarloDraws=20
                    Computer=jpype.JClass('com.conveyal.r5.analyst.TravelTimeComputer')
                    result=self._parse_results(from_id,Computer(task,self.transport_network).computeTravelTimes()).rename(columns={'travel_time_p90':'travel_time'})
                    local_windows={int(r.to_id):10 for r in result.itertuples()}
                    missing=result.travel_time.isna()
                    # Large mode only broadens an origin if it found no other
                    # destination. Missing cells remain unavailable search arcs.
                    nonself=result[result.to_id.astype(str)!=str(from_id)]
                    retry=request.get('planningMode')!='large' or (len(nonself)>0 and nonself.travel_time.isna().all())
                    if missing.any() and request['modes'] and retry:
                        send('progress',message=f'R5: widening the departure window for missing connections from origin {from_id}.')
                        task.departure_time_window=timedelta(minutes=60)
                        wider=self._parse_results(from_id,Computer(task,self.transport_network).computeTravelTimes()).rename(columns={'travel_time_p90':'travel_time'})
                        for index in result.index[missing]:
                            if math.isfinite(wider.loc[index,'travel_time']):
                                result.loc[index,'travel_time']=wider.loc[index,'travel_time']
                                local_windows[int(result.loc[index,'to_id'])]=60
                    rows=[dict(fromIndex=int(r.from_id),toIndex=int(r.to_id),seconds=None if not math.isfinite(r.travel_time) else math.ceil(r.travel_time*60),departureWindowMinutes=local_windows[int(r.to_id)]) for r in result.itertuples()]
                    with progress_lock:
                        done+=1
                        windows.update({(int(from_id),key):value for key,value in local_windows.items()})
                        send('progress',message=f'R5: {done} of {total} origins calculated.',extra=dict(done=done,total=total,matrixRows=rows))
                    return result
            matrix=ProgressMatrix(network,origins=frame,destinations=frame,
                departure=datetime.fromisoformat(request['departure']).replace(tzinfo=None),
                percentiles=[90],departure_time_window=timedelta(minutes=10),max_time=timedelta(hours=4),
                transport_modes=[r5py.TransportMode.TRANSIT] if request['modes'] else [r5py.TransportMode.WALK])
            rows=[dict(fromIndex=int(r.from_id),toIndex=int(r.to_id),seconds=None if not math.isfinite(r.travel_time) else math.ceil(r.travel_time*60),departureWindowMinutes=windows[int(r.from_id),int(r.to_id)]) for r in matrix.itertuples()]
            send('result',rows=rows,buildSeconds=round(build,3),computeSeconds=round(time.monotonic()-stamp,3))
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            send('error',message='R5 could not prepare this request: '+str(exc)[:1200])


if __name__=='__main__':main()
