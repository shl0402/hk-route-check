#!/usr/bin/env python3
"""Cache official bus/minibus static APIs; preserve source identities and uncertainty.

No live prediction is installed into a static GTFS. Frequency ranges remain ranges;
a single departure needs no frequency and either no end or one identical clock.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
from html import unescape
import json
import math
from pathlib import Path
import re
import threading
import time
from urllib.parse import quote

KMB = 'https://data.etabus.gov.hk/v1/transport/kmb'
CTB = 'https://rt.data.gov.hk/v2/transport/citybus'
GMB = 'https://data.etagmb.gov.hk'
DOCS = {
    'kmb-spec.pdf': 'https://data.etabus.gov.hk/datagovhk/kmb_eta_api_specification.pdf',
    'kmb-dictionary.pdf': 'https://data.etabus.gov.hk/datagovhk/kmb_eta_data_dictionary.pdf',
    'citybus-spec.pdf': 'https://www.citybus.com.hk/datagovhk/bus_eta_api_specifications.pdf',
    'gmb-spec.pdf': GMB + '/static/GMB_ETA_API_Specification.pdf',
    'td-gmb28-single-departures.html': 'https://www.td.gov.hk/tc/traffic_notices/index_id_52472.html',
}

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def save(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.part')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)

class Cache:
    def __init__(self, root, offline=False, refresh=False, workers=4):
        if offline and refresh:
            raise ValueError('--offline and --refresh cannot be combined')
        self.raw = Path(root) / 'data/transit_enrichment/raw/official'
        self.raw.mkdir(parents=True, exist_ok=True)
        self.mp = self.raw / 'manifest.json'
        self.manifest = json.loads(self.mp.read_text()) if self.mp.exists() else {}
        self.offline, self.refresh = offline, refresh
        self.workers = max(1, min(int(workers), 4))
        self.lock = threading.Lock(); self.rate_lock = threading.Lock()
        self.next_request = 0.; self.thread = threading.local()
        self.downloaded = 0; self.hits = 0

    def get(self, name, url):
        path = self.raw / name
        with self.lock:
            meta = self.manifest.get(name)
        if path.exists() and not self.refresh:
            if not meta or meta.get('url') != url or meta.get('sha256') != sha(path):
                raise ValueError('Changed/unverified official source: ' + name)
            with self.lock: self.hits += 1
            return json.loads(path.read_bytes()) if name.endswith('.json') else None
        if self.offline:
            raise ValueError('Missing official source cache: ' + name)
        import requests
        if not hasattr(self.thread, 'session'):
            self.thread.session = requests.Session()
            self.thread.session.headers['User-Agent'] = 'map-routing-research/1.0 (static open-data cache)'
        for attempt in range(4):
            with self.rate_lock:
                time.sleep(max(0, self.next_request - time.monotonic()))
                self.next_request = time.monotonic() + .25  # <=4 request starts/sec, <=4 in flight
            try:
                response = self.thread.session.get(url, timeout=(15, 60))
                if response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(min(30, max(2 ** (attempt + 1), int(response.headers.get('Retry-After', '0')))))
                    continue
                response.raise_for_status()
                content = response.content
                if not content: raise ValueError('Empty official source: ' + url)
                if name == 'td-gmb28-single-departures.html':
                    notice=re.sub(r'\s+','',unescape(re.sub(r'<[^>]+>','',content.decode('utf-8',errors='replace'))))
                    if not all(word in notice for word in ('28S','7時20分','2018')):
                        raise ValueError('TD single departure corroboration page changed')
                if name.endswith('.json'):
                    result = response.json()
                    if not isinstance(result, dict) or 'data' not in result:
                        raise ValueError('Unexpected API response wrapper: ' + url)
                elif name.endswith('.pdf') and not content.startswith(b'%PDF'):

                    raise ValueError('Expected PDF source: ' + url)
                else: result = None
                break
            except requests.RequestException:
                if attempt == 3: raise
                time.sleep(2 ** (attempt + 1))
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + '.part'); temp.write_bytes(content); temp.replace(path)
        meta = dict(url=url, sha256=sha(path), retrieved_at=datetime.now(timezone.utc).isoformat(),
                    bytes=len(content), content_type=response.headers.get('Content-Type'))
        if isinstance(result, dict):
            meta['generated_timestamp'] = result.get('generated_timestamp')
            meta['data_timestamp'] = result.get('data_timestamp')
        with self.lock:
            self.manifest[name] = meta; self.downloaded += 1; save(self.mp, self.manifest)
        return result

    def batch(self, label, pairs):
        pairs = list(dict(pairs).items()); errors=[]
        print(f'Official {label}: {len(pairs)} sources', flush=True)
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            pending={pool.submit(self.get,name,url):name for name,url in pairs}
            for i, f in enumerate(as_completed(pending), 1):
                try: f.result()
                except Exception as e:
                    errors.append(dict(file=pending[f], error=str(e)))
                    print(f'Official source failed: {pending[f]}: {e}',flush=True)
                if i % 100 == 0 or i == len(pairs):
                    print(f'Official {label}: {i}/{len(pairs)}; downloads {self.downloaded}, cache {self.hits}, errors {len(errors)}',flush=True)
        if errors:
            save(self.raw/'fetch_errors.json', errors)
            raise ValueError(f'{len(errors)} official source failures; cached progress preserved: {errors[:3]}')

    def read(self, name):
        return json.loads((self.raw/name).read_text())

def fetch(root, offline=False, refresh=False, workers=4):
    c = Cache(root, offline, refresh, workers)
    c.batch('directories', list(DOCS.items()) + [
        ('kmb/routes.json', KMB+'/route/'), ('kmb/route-stops.json', KMB+'/route-stop'),
        ('kmb/stops.json', KMB+'/stop'), ('ctb/routes.json', CTB+'/route/CTB'), ('gmb/routes.json', GMB+'/route')])
    cr = c.read('ctb/routes.json')['data']
    gr = c.read('gmb/routes.json')['data']['routes']
    c.batch('route variants and stop sequences',
        [(f'ctb/route-stops/{r["route"]}-{d}.json', CTB+f'/route-stop/CTB/{quote(r["route"],safe="")}/{d}')
            for r in cr for d in ('outbound','inbound')] +
        [(f'gmb/routes/{region}-{code}.json', GMB+f'/route/{region}/{quote(code,safe="")}')
            for region,codes in gr.items() for code in codes])
    gdirs = {(str(r['route_id']),str(d['route_seq'])) for region,codes in gr.items() for code in codes
             for r in c.read(f'gmb/routes/{region}-{code}.json')['data'] for d in r['directions']}
    c.batch('minibus stop sequences', [(f'gmb/route-stops/{rid}-{d}.json',GMB+f'/route-stop/{rid}/{d}') for rid,d in sorted(gdirs)])
    cs = {s['stop'] for r in cr for d in ('outbound','inbound')
          for s in c.read(f'ctb/route-stops/{r["route"]}-{d}.json')['data']}
    gs = {str(s['stop_id']) for rid,d in gdirs for s in c.read(f'gmb/route-stops/{rid}-{d}.json')['data']['route_stops']}
    c.batch('stop coordinates', [(f'ctb/stops/{sid}.json', CTB+'/stop/'+sid) for sid in sorted(cs)] +
            [(f'gmb/stops/{sid}.json', GMB+'/stop/'+sid) for sid in sorted(gs)])
    # One bounded sample per operator documents current schema. Samples never become static departures.
    ksample = next(r for r in c.read('kmb/route-stops.json')['data'] if r['route']=='98D' and r['bound']=='O' and str(r['service_type'])=='1')
    nsample = c.read('ctb/route-stops/N796-outbound.json')['data'][0]
    gsample = c.read('gmb/routes/NT-112M.json')['data'][0]
    c.batch('ETA schema samples', [
        ('samples/kmb-98D.json',KMB+f'/eta/{ksample["stop"]}/98D/1'),
        ('samples/ctb-N796.json',CTB+f'/eta/CTB/{nsample["stop"]}/N796'),
        ('samples/gmb-112M.json',GMB+f'/eta/route-stop/{gsample["route_id"]}/1/1')])
    (c.raw/'fetch_errors.json').unlink(missing_ok=True)
    return c.manifest

def verify(root):
    raw=Path(root)/'data/transit_enrichment/raw/official'; mp=raw/'manifest.json'
    manifest=json.loads(mp.read_text())
    if not manifest: raise ValueError('Empty official source manifest')
    for name,meta in manifest.items():
        if sha(raw/name)!=meta['sha256']: raise ValueError('Changed official source: '+name)
    return manifest

def _coordinates(lat, lon):
    lat,lon=float(lat),float(lon)
    if not math.isfinite(lat) or not math.isfinite(lon) or not (21.8<=lat<=22.7 and 113.7<=lon<=114.5):
        raise ValueError(f'Coordinates outside HK service envelope: {lat},{lon}')
    return lat,lon

def clock_seconds(s):
    if not isinstance(s,str) or not re.fullmatch(r'\d{2}:\d{2}:\d{2}',s): raise ValueError('Invalid clock: '+str(s))
    h,m,sec=map(int,s.split(':'))
    if h>47 or m>59 or sec>59: raise ValueError('Invalid clock: '+s)
    return h*3600+m*60+sec

def normalize_headway(h):
    w=h.get('weekdays'); ph=h.get('public_holiday')
    if not isinstance(w,list) or len(w)!=7 or any(type(x) is not bool for x in w) or type(ph) is not bool:
        raise ValueError('Unknown service-day/public-holiday rule')
    start=clock_seconds(h.get('start_time')); end=h.get('end_time')
    freq=h.get('frequency'); upper=h.get('frequency_upper')
    base=dict(weekdays=w,public_holiday=ph,headway_seq=h.get('headway_seq'),start_time=h['start_time'],
              end_time=end,start_seconds=start,raw=h)
    if freq is None and upper is None and (end is None or end == h['start_time']):
        return dict(base,kind='departure',end_seconds=None,frequency_seconds=None,frequency_upper_seconds=None,
                    normalization_note='Official API supplies one clock and no frequency; identical start/end is a point departure, corroborated against TD route 28 notice.' if end is not None else None)
    if end is None or isinstance(freq,bool) or not isinstance(freq,(int,float)) or freq<=0:
        raise ValueError('Unusable frequency/end-time combination')
    if upper is not None and (isinstance(upper,bool) or not isinstance(upper,(int,float)) or upper<freq):
        raise ValueError('Invalid upper headway bound')
    end_sec=clock_seconds(end)
    if end_sec<start: end_sec+=86400
    if end_sec==start: raise ValueError('Zero-width frequency band is not an explicit single departure')
    return dict(base,kind='frequency',end_seconds=end_sec,frequency_seconds=int(freq*60),
                frequency_upper_seconds=int(upper*60) if upper is not None else None)

def _ordered(stops):
    ordered=sorted(stops,key=lambda r:r['seq'])
    seq=[s['seq'] for s in ordered]
    if not seq or seq!=list(range(1,len(seq)+1)): raise ValueError('Missing/duplicate/nonconsecutive ordered stops')
    if len(seq)<2: raise ValueError('Fewer than two stops')
    return ordered

def normalize(root):
    root=Path(root); raw=root/'data/transit_enrichment/raw/official'; manifest=verify(root)
    def read(name):
        if name not in manifest: raise ValueError('Unverified official input: '+name)
        return json.loads((raw/name).read_text())['data']
    def meta(name): return dict(source_url=manifest[name]['url'],source_file=name,
                               source_sha256=manifest[name]['sha256'],retrieved_at=manifest[name]['retrieved_at'])
    patterns=[]; stops={}; rejected=[]
    def stop(operator,sid,lat,lon,names,filename,**extra):
        lat,lon=_coordinates(lat,lon); sid=str(sid); key=operator+':'+sid
        record=dict(stop_id=key,native_stop_id=sid,operator=operator,lat=lat,lon=lon,
                    name_en=names.get('name_en'),name_tc=names.get('name_tc'),**meta(filename),**extra)
        stops[key]=record; return record
    def reject(kind,key,e): rejected.append(dict(kind=kind,key=str(key),reason=str(e)))
    for s in read('kmb/stops.json'):
        try: stop('KMB',s['stop'],s['lat'],s['long'],s,'kmb/stops.json')
        except (ValueError,KeyError,TypeError) as e: reject('stop','KMB:'+s.get('stop',''),e)
    kseq=defaultdict(list)
    for s in read('kmb/route-stops.json'):
        kseq[(s['route'],s['bound'],str(s['service_type']))].append(s)
    def finish(p,visits):
        visits=_ordered(visits); p.update(stops=visits,stop_ids=[s['stop_id'] for s in visits],
            native_stop_ids=[s['native_stop_id'] for s in visits]);patterns.append(p)
    for r in read('kmb/routes.json'):
        key=(r['route'],r['bound'],str(r['service_type'])); pid='KMB:'+':'.join(key)
        try:
            visits=[dict(stops['KMB:'+s['stop']],seq=int(s['seq'])) for s in kseq[key]]
            finish(dict(key=pid,operator='KMB',route=r['route'],direction=r['bound'],service_type=str(r['service_type']),
                        orig_en=r['orig_en'],orig_tc=r['orig_tc'],dest_en=r['dest_en'],dest_tc=r['dest_tc'],headways=[],
                        source_files=['kmb/routes.json','kmb/route-stops.json','kmb/stops.json'],**meta('kmb/routes.json')),visits)
        except (ValueError,KeyError,TypeError) as e: reject('pattern',pid,e)
    for r in read('ctb/routes.json'):
        for direction,d in [('O','outbound'),('I','inbound')]:
            fn=f'ctb/route-stops/{r["route"]}-{d}.json'; rs=read(fn); pid=f'CTB:{r["route"]}:{direction}'
            if not rs: continue
            try:
                visits=[]
                for s in rs:
                    sid=s['stop']; sn=f'ctb/stops/{sid}.json'
                    if s['route']!=r['route'] or s['dir']!=direction: raise ValueError('Route-stop identity mismatch')
                    if 'CTB:'+sid not in stops:
                        sr=read(sn); stop('CTB',sid,sr['lat'],sr['long'],sr,sn)
                    visits.append(dict(stops['CTB:'+sid],seq=int(s['seq'])))
                finish(dict(key=pid,operator='CTB',route=r['route'],direction=direction,service_type=None,
                    orig_en=r['orig_en'] if direction=='O' else r['dest_en'],orig_tc=r['orig_tc'] if direction=='O' else r['dest_tc'],
                    dest_en=r['dest_en'] if direction=='O' else r['orig_en'],dest_tc=r['dest_tc'] if direction=='O' else r['orig_tc'],
                    headways=[],variant_warning='Citybus API does not expose a service_type; validate full stop order before matching special variants.',
                    source_files=['ctb/routes.json',fn],**meta(fn)),visits)
            except (ValueError,KeyError,TypeError) as e: reject('pattern',pid,e)
    for region,codes in read('gmb/routes.json')['routes'].items():
        for code in codes:
            fn=f'gmb/routes/{region}-{code}.json'
            for r in read(fn):
                rid=str(r['route_id'])
                for d in r['directions']:
                    direction=str(d['route_seq']); pid=f'GMB:{rid}:{direction}'; sn=f'gmb/route-stops/{rid}-{direction}.json'
                    try:
                        if r.get('region',region)!=region or r.get('route_code',code)!=code:raise ValueError('Minibus route identity mismatch')
                        visits=[]
                        for s in read(sn)['route_stops']:
                            sid=str(s['stop_id']);sf=f'gmb/stops/{sid}.json'; sr=read(sf)
                            if sr.get('enabled') is not True: raise ValueError('Disabled official stop '+sid)
                            coord=sr['coordinates']['wgs84']
                            item=stop('GMB',sid,coord['latitude'],coord['longitude'],s,sf,enabled=True)
                            visits.append(dict(item,seq=int(s['stop_seq'])))
                        hs=[]; headway_errors=[]
                        for h in d['headways']:
                            try: hs.append(normalize_headway(h))
                            except (ValueError,KeyError,TypeError) as e: headway_errors.append(dict(raw=h,reason=str(e)))
                        finish(dict(key=pid,operator='GMB',route=code,region=region,route_id=rid,direction=direction,
                            service_type=None,description_en=r.get('description_en'),description_tc=r.get('description_tc'),
                            orig_en=d['orig_en'],orig_tc=d['orig_tc'],dest_en=d['dest_en'],dest_tc=d['dest_tc'],
                            remarks_en=d.get('remarks_en'),remarks_tc=d.get('remarks_tc'),headways=hs,headway_errors=headway_errors,
                            timetable_usable=bool(hs) and not headway_errors,
                            data_timestamp=d.get('data_timestamp'),source_files=[fn,sn],**meta(fn)),visits)
                    except (ValueError,KeyError,TypeError) as e: reject('pattern',pid,e)
    keys=[p['key'] for p in patterns]
    if len(keys)!=len(set(keys)): raise ValueError('Duplicate official pattern identity')
    counts=Counter(p['operator'] for p in patterns)
    summary=dict(patterns=dict(counts),stops=dict(Counter(s['operator'] for s in stops.values())),rejected=len(rejected),
        gmb_headway_bands=sum(h['kind']=='frequency' for p in patterns for h in p['headways']),
        gmb_single_departures=sum(h['kind']=='departure' for p in patterns for h in p['headways']),
        gmb_patterns_with_unusable_timetable=sum(p['operator']=='GMB' and not p['timetable_usable'] for p in patterns))
    available_routes=([dict(operator='KMB',route=number) for number in sorted({r['route'] for r in read('kmb/routes.json')})] +
        [dict(operator='CTB',route=number) for number in sorted({r['route'] for r in read('ctb/routes.json')})] +
        [dict(operator='GMB',region=region,route=number) for region,numbers in read('gmb/routes.json')['routes'].items() for number in numbers])
    data=dict(format=1,patterns=patterns,stops=stops,available_routes=available_routes,source_manifest=manifest,summary=summary,validation=dict(rejected=rejected),
        policy='Official source snapshots; weekday and public-holiday rules preserved separately. Headway ranges are not exact departures. Intermediate stop times are not supplied. ETA samples are excluded from static schedules.')
    save(root/'data/transit_enrichment/official.json',data)
    print(json.dumps(summary,ensure_ascii=False),flush=True); return data

def load(root):
    # Re-normalizing verifies raw bytes and prevents consuming manually altered derived data.
    return normalize(root)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['fetch','normalize','verify'])
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument('--offline',action='store_true');p.add_argument('--refresh',action='store_true');p.add_argument('--workers',type=int,default=4)
    a=p.parse_args()
    if a.command=='fetch':fetch(a.root,a.offline,a.refresh,a.workers)
    if a.command=='verify':print(f'Verified {len(verify(a.root))} official sources')
    else:normalize(a.root)
