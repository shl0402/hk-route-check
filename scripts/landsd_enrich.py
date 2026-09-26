#!/usr/bin/env python3
"""Download LandsD source data and conservatively enrich an existing GTFS.

No times, accessibility flags, coordinates, floors or connecting paths are invented.
General POIs and unlinked indoor geometry are supplemental data, not transit stops.
"""
from __future__ import annotations
import argparse, collections, csv, hashlib, io, json, re, shutil, time, unicodedata
import urllib.parse, urllib.request, zipfile
import requests
from datetime import datetime, timezone
from pathlib import Path

IGEO = 'https://open.hkmapservice.gov.hk/OpenData/directDownload?productName=iGeoCom&sheetName=iGeoCom&productFormat=GEOJSON'
DOC = 'https://portal.csdi.gov.hk/csdi-webpage/apidoc/3d-indoor-mtr-station-map'
WFS = 'https://mapapi.hkmapservice.gov.hk/ogc/wfs/indoor/'
LAYERS = ('mtr_level_polygon', 'mtr_amenity_point', 'mtr_opening_line', 'mtr_unit_polygon', 'mtr_occupant_point')

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''): h.update(b)
    return h.hexdigest()

def save(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.part')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)

def url(layer, venue=None):
    q = dict(service='WFS', version='1.1.0', request='GetFeature', outputFormat='application/json')
    if venue: q['cql_filter'] = "venue_id='" + venue + "'"
    return WFS + layer + '?' + urllib.parse.urlencode(q)

def features(path):
    data = json.loads(Path(path).read_text())
    if data.get('type') != 'FeatureCollection': raise ValueError(f'Not GeoJSON: {path}')
    fs = data['features']
    total = data.get('totalFeatures', len(fs))
    if total != 'unknown' and int(total) != len(fs):
        raise ValueError(f'Truncated WFS response: {path}: {len(fs)}/{total}')
    return fs

def fetch(root, offline=False, refresh=False):
    cache = root / 'data/landsd/raw'; cache.mkdir(parents=True, exist_ok=True)
    mp = cache / 'manifest.json'
    manifest = json.loads(mp.read_text()) if mp.exists() else {}
    def get(name, source, geo=True):
        p = cache / name
        record = manifest.get(name, {})
        if not refresh and p.exists() and record.get('sha256') == sha(p):
            if geo: features(p)
            return p
        if offline: raise ValueError('Missing/unverified LandsD cache: ' + str(p))
        p.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(4):
            try:
                response = requests.get(source, headers={'User-Agent':'HKRouteCheck-research/1.0'}, timeout=90)
                response.raise_for_status()
                body = response.content
                temp = p.with_suffix(p.suffix + '.part'); temp.write_bytes(body)
                if geo: features(temp)
                else:
                    with zipfile.ZipFile(temp) as z:
                        if z.testzip(): raise ValueError('Corrupt source archive')
                temp.replace(p)
                manifest[name] = dict(url=source, retrieved_at=datetime.now(timezone.utc).isoformat(), sha256=sha(p), bytes=p.stat().st_size)
                save(mp, manifest)
                time.sleep(0.4)
                return p
            except Exception:
                if attempt == 3: raise
                time.sleep(2 ** attempt)
    get('igeocom.zip', IGEO, False)
    venues = features(get('venues.geojson', url('mtr_venue_polygon')))
    for i, f in enumerate(venues, 1):
        v = f['properties']['venue_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', v): raise ValueError('Invalid venue ID')
        for layer in LAYERS: get(v + '/' + layer + '.geojson', url(layer, v))
        print(f'[{i}/{len(venues)}] {f["properties"]["venue_name_en"]}: {len(LAYERS)} layers verified/cached', flush=True)
        save(root/'data/landsd/progress.json',dict(completed_stations=i,total_stations=len(venues),station=f['properties']['venue_name_en'],status='complete' if i==len(venues) else 'running'))
    return cache

def namekey(value):
    s = unicodedata.normalize('NFKC', (value or '').split(' · ')[0]).casefold().strip()
    s = re.sub(r'^(mass transit railway|mtr|lr)[\s\-]+', '', s)
    s = re.sub(r'\s+(light rail stop|station|stop)$', '', s)
    return re.sub(r'[\s\-–()]+', '', s)

def point(feature):
    g = feature.get('geometry') or {}
    if g.get('type') != 'Point': return None
    c = g['coordinates']
    if len(c) < 2 or not 113.8 <= c[0] <= 114.5 or not 22.08 <= c[1] <= 22.6: return None
    return c

def csv_bytes(rows, fields):
    s = io.StringIO(newline=''); w = csv.DictWriter(s, fields, lineterminator='\n', extrasaction='raise')
    w.writeheader(); w.writerows({k: r.get(k, '') for k in fields} for r in rows)
    return s.getvalue().encode()

def merge(root, source, output):
    cache = fetch(root, offline=True)
    gen = root / 'data/landsd'; gen.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((cache/'manifest.json').read_text())
    with zipfile.ZipFile(cache/'igeocom.zip') as z:
        n = next(n for n in z.namelist() if n.lower().endswith('.geojson'))
        places = json.loads(z.read(n))
    save(gen/'places.geojson', places)
    with zipfile.ZipFile(source) as z:
        def table(n):
            return list(csv.DictReader(io.TextIOWrapper(z.open(n), encoding='utf-8-sig'))) if n in z.namelist() else []
        stops = table('stops.txt'); levels = table('levels.txt'); translations = table('translations.txt'); attrs = table('attributions.txt')
        rail_proof = json.loads(z.read('mtr_api_provenance.json')) if 'mtr_api_provenance.json' in z.namelist() else {}
        unresolved_lr = sum(v['total']-v['applied'] for k,v in rail_proof.get('line_coverage',{}).items() if k.startswith('LRT:'))
        fixed_lr_transfers = sum(r.get('min_transfer_time')=='300' and r['from_stop_id'].startswith('RAIL:LRT:') for r in table('transfers.txt'))
        # Re-running on an enriched feed is idempotent.
        stops = [s for s in stops if not s['stop_id'].startswith('LANDSD:')]
        levels = [s for s in levels if not s['level_id'].startswith('LANDSD:')]
        translations = [s for s in translations if not s.get('record_id','').startswith('LANDSD:')]
        attrs = [s for s in attrs if s.get('attribution_id') != 'LANDSD']
        byname = collections.defaultdict(list)
        for s in stops:
            if s.get('location_type') == '1' and s['stop_id'].startswith('RAIL:'):
                byname[(s['stop_id'].split(':')[1], namekey(s['stop_name']))].append(s)
        proof = dict(version=1, source='Lands Department', source_url=DOC, input_sha256=sha(source),
                     policy='Only source-backed values. Existing timing/transfer fallbacks preserved. No new estimated times or connections.',
                     files=manifest, stops={}, station_matches=[], withheld=[])
        def translated(table_name, field, rid, zh):
            if zh: translations.append(dict(table_name=table_name, field_name=field, language='zh-Hant', translation=zh, record_id=rid))
        # Match only explicitly named MTR/LR stations. Never nearest-neighbour-match an entrance.
        for f in places['features']:
            p = f['properties']; typ = p.get('TYPE'); c = point(f)
            if typ not in ('RSN', 'LRA', 'MTA') or c is None: continue
            system = 'LRT' if typ == 'LRA' else 'MTR'
            station_name = p.get('E_SITENAME') if typ == 'MTA' else p.get('ENGLISHNAME')
            if typ == 'MTA' and not station_name:
                named = re.fullmatch(r'(Mass Transit Railway .+ Station)-[^-]+ Access', p.get('ENGLISHNAME') or '')
                if named: station_name = named.group(1)
            matches = byname.get((system, namekey(station_name)), [])
            if len(matches) != 1:
                proof['withheld'].append(dict(source='iGeoCom', id=p['GEONAMEID'], name=p['ENGLISHNAME'], reason='ambiguous parent station' if matches else 'no exact parent station', candidates=[s['stop_id'] for s in matches])); continue
            parent = matches[0]
            if typ in ('RSN','LRA'):
                # Parent coordinates locate the station, not a platform. Do not propagate them to platforms.
                old = {k:parent.get(k,'') for k in ('stop_lat','stop_lon','stop_desc')}
                parent.update(stop_lat=str(c[1]), stop_lon=str(c[0]))
                if not parent.get('stop_desc'): parent['stop_desc'] = p.get('E_ADDRESS') or ''
                proof['stops'][parent['stop_id']] = dict(source='iGeoCom', source_id=p['GEONAMEID'], revision=p.get('REV_DATE'), previous=old, fields=['stop_lat','stop_lon','stop_desc'])
                # Avoid repeated translations on re-enrichment.
                translations = [t for t in translations if not (t.get('table_name')=='stops' and t.get('record_id')==parent['stop_id'] and t.get('field_name')=='stop_name' and t.get('language')=='zh-Hant')]
                translated('stops','stop_name',parent['stop_id'],p.get('CHINESENAME'))
            else:
                sid = 'LANDSD:ACCESS:' + str(p['GEONAMEID'])
                # A MTA point is a published railway access. SUBCAT describes equipment,
                # not a guarantee of a complete wheelchair-accessible route.
                stops.append(dict(stop_id=sid, stop_name=p['ENGLISHNAME'], stop_lat=str(c[1]), stop_lon=str(c[0]), location_type='2', parent_station=parent['stop_id'], stop_desc=p.get('E_ADDRESS') or '', stop_url='https://www.landsd.gov.hk/en/survey-mapping/mapping/other-products/iGeoCom.html'))
                translated('stops','stop_name',sid,p.get('CHINESENAME'))
                proof['stops'][sid] = dict(source='iGeoCom', source_id=p['GEONAMEID'], revision=p.get('REV_DATE'), subtype=p.get('SUBCAT'), fields=['stop_name','stop_lat','stop_lon','parent_station','stop_desc'])
        facilities=[]; layout=[]; categories=collections.Counter()
        for v in features(cache/'venues.geojson'):
            vp=v['properties']; vid=vp['venue_id']; matches=byname.get(('MTR',namekey(vp['venue_name_en'])),[])
            proof['station_matches'].append(dict(venue_id=vid,name=vp['venue_name_en'],stop_ids=[s['stop_id'] for s in matches],status='exact' if len(matches)==1 else 'ambiguous' if matches else 'unmatched'))
            # Floor indices are published ordinal values; no z-to-floor inference.
            for f in features(cache/vid/'mtr_level_polygon.geojson'):
                p=f['properties']; ordinal=p.get('level_ordinal')
                if len(matches)==1 and isinstance(ordinal,(int,float)):
                    lid='LANDSD:LEVEL:'+p['level_id']
                    levels.append(dict(level_id=lid,level_index=str(ordinal),level_name=p.get('level_name_en') or p.get('level_short_name_en') or ''))
                    translated('levels','level_name',lid,p.get('level_name_zh'))
                layout.append(f)
            for layer in LAYERS[1:]:
                fs=features(cache/vid/(layer+'.geojson'))
                if layer=='mtr_amenity_point':
                    facilities.extend(fs); categories.update(f['properties'].get('amenity_category') for f in fs)
                    for f in fs:
                        p=f['properties']; c=point(f)
                        if len(matches)!=1 or (p.get('amenity_category') or '').lower()!='platform' or c is None: continue
                        sid='LANDSD:PLATFORM:'+p['amenity_id']
                        lid='LANDSD:LEVEL:'+str(p.get('level_id') or '')
                        entry=dict(stop_id=sid,stop_name=vp['venue_name_en']+' — '+(p.get('amenity_name_en') or 'Platform'),stop_lat=str(c[1]),stop_lon=str(c[0]),location_type='0',parent_station=matches[0]['stop_id'],stop_url=DOC)
                        if any(x['level_id']==lid for x in levels):entry['level_id']=lid
                        code=re.match(r'^Platform\s+(\d+[A-Za-z]?)(?:\s|$)',p.get('amenity_name_en') or '',re.I)
                        if code:entry['platform_code']=code.group(1)
                        stops.append(entry)
                        translated('stops','stop_name',sid,(vp.get('venue_name_zh') or '')+' — '+(p.get('amenity_name_zh') or ''))
                        proof['stops'][sid]=dict(source='3D Indoor MTR Station Map',venue_id=vid,source_id=p['amenity_id'],fields=list(entry),trip_assignment='unassigned reference platform; original logical boarding stops retained')
        # Indoor "entry" includes concourse-side points. Do NOT silently make these
        # street entrances or infer a path to an existing schematic GTFS platform.
        supplemental=dict(type='FeatureCollection',features=facilities)
        save(gen/'station_facilities.geojson', supplemental)
        save(gen/'station_levels.geojson', dict(type='FeatureCollection',features=layout))
        attrs.append(dict(attribution_id='LANDSD',organization_name='Lands Department, Hong Kong SAR Government',is_producer='1',is_operator='0',is_authority='0',attribution_url='https://www.landsd.gov.hk/en/spatial-data/open-data.html'))
        # GTFS translations requires feed_info, whose publisher URL is not known
        # for a local unpublished feed. Use bilingual names, not invented publisher details.
        if 'feed_info.txt' not in z.namelist():
            if 'translations.txt' in z.namelist(): raise ValueError('Input translations require feed_info.txt')
            indices={'stops':{s['stop_id']:s for s in stops},'levels':{s['level_id']:s for s in levels}}
            for tr in translations:
                row=indices.get(tr['table_name'],{}).get(tr.get('record_id'))
                if row is not None and tr['field_name'] in row:
                    field=tr['field_name'];suffix=' · '+tr['translation']
                    if not row[field].endswith(suffix):row[field]+=suffix
                    if tr['table_name']=='stops' and row['stop_id'] in proof['stops']:
                        proof['stops'][row['stop_id']]['fields']=sorted(set(proof['stops'][row['stop_id']]['fields']+[field]))
            translations=[]
        # Preserve the existing source tables byte-for-byte unless explicitly enriched.
        replaced={'stops.txt','levels.txt','translations.txt','attributions.txt','landsd_provenance.json','landsd_facilities.geojson'}
        output.parent.mkdir(parents=True,exist_ok=True); temp=output.with_suffix('.tmp')
        with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED) as dst:
            for info in z.infolist():
                if info.filename not in replaced:
                    with z.open(info) as r,dst.open(info.filename,'w') as w: shutil.copyfileobj(r,w)
            for name,rows,required in [
                ('stops.txt',stops,['stop_id','stop_name','stop_lat','stop_lon','location_type','parent_station']),
                ('levels.txt',levels,['level_id','level_index','level_name']),
                ('translations.txt',translations,['table_name','field_name','language','translation','record_id']),
                ('attributions.txt',attrs,['attribution_id','organization_name','is_producer','is_operator','is_authority','attribution_url'])]:
                if not rows: continue
                fields=list(dict.fromkeys(required+[k for row in rows for k in row]))
                dst.writestr(name,csv_bytes(rows,fields))
            dst.writestr('landsd_provenance.json',json.dumps(proof,ensure_ascii=False))
            dst.writestr('landsd_facilities.geojson',json.dumps(supplemental,ensure_ascii=False))
        temp.replace(output)
    summary=dict(places=len(places['features']), station_venues=len(proof['station_matches']),
        station_matches=dict(collections.Counter(s['status'] for s in proof['station_matches'])),
        station_coordinates_updated=sum(not s.startswith('LANDSD:') for s in proof['stops']),
        entrances_added=sum(s.startswith('LANDSD:ACCESS:') for s in proof['stops']),
        reference_platforms_added=sum(s.startswith('LANDSD:PLATFORM:') for s in proof['stops']),
        levels_added=sum(s['level_id'].startswith('LANDSD:') for s in levels),
        station_facilities=len(facilities),facility_categories=dict(categories), withheld_records=len(proof['withheld']),
        invented_values=0,new_pathways=0,timing_values_changed=0,output=str(output),output_sha256=sha(output),
        unresolved_light_rail_segments_retained=unresolved_lr,fixed_300_second_light_rail_transfers_retained=fixed_lr_transfers,
        limitations=['Indoor facilities are supplemental; OTP does not read that extension.',
        'Published physical platforms are reference stops with levels; trips still use existing logical boarding stops. No directional assignment guessed.',
        'No verified mapping of directional GTFS platforms to network endpoints; no pathways invented.',
        'No LandsD measured walking/transfer times found. Existing fallback times retained.',
        'General POIs are in data/landsd/places.geojson; not represented as transit stops.',
        'Shared Airport Express/Tung Chung parent names are withheld rather than assigned twice.'])
    save(gen/'merge_report.json',summary)
    (gen/'RESULTS.md').write_text('# LandsD enrichment\n\n'+ '\n'.join(f'- {k}: **{v}**' for k,v in summary.items() if isinstance(v,int))+'\n\n'+ '\n'.join('- '+s for s in summary['limitations'])+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['fetch','merge']);p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--offline',action='store_true');p.add_argument('--refresh',action='store_true');p.add_argument('--input',type=Path);p.add_argument('--output',type=Path)
    a=p.parse_args()
    if a.command=='fetch':fetch(a.root,a.offline,a.refresh)
    else:
        merge(a.root,a.input or a.root/'data/generated/hk-transit-MTR-API.gtfs.zip',a.output or a.root/'data/generated/hk-transit-LANDSD.gtfs.zip')
if __name__=='__main__':main()
