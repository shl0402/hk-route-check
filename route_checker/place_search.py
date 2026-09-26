"""Offline place descriptions. Keep separate source features, even at the same point."""
import csv
import io
import json
import math
import re
import zipfile
from collections import defaultdict

SCHEMA = 2


def joined(*values):
    return ' · '.join(dict.fromkeys(str(v).strip() for v in values if v and str(v).strip()))


def osm_description(tags, feature_type):
    """Interpret explicit tags; never infer minibus/platform numbers from proximity."""
    pt, rail = tags.get('public_transport'), tags.get('railway')
    bus = tags.get('bus') == 'yes' or tags.get('highway') == 'bus_stop'
    mini = tags.get('minibus') == 'yes'
    rail_mode = 'Light rail' if tags.get('light_rail') == 'yes' else 'Rail'
    if rail == 'subway_entrance':
        kind = 'MTR entrance' if tags.get('network') == 'MTR' else 'Rail entrance'
    elif rail == 'station' or pt == 'station' and any(tags.get(k) == 'yes' for k in ('train', 'subway', 'light_rail', 'tram')):
        kind = 'MTR station' if tags.get('network') == 'MTR' else rail_mode + ' station'
    elif rail == 'stop' or pt == 'stop_position' and any(tags.get(k) == 'yes' for k in ('train', 'subway', 'light_rail', 'tram')):
        kind = rail_mode + ' stopping point (on track)'
    elif rail == 'platform':
        kind = rail_mode + ' platform'
    elif tags.get('amenity') == 'bus_station':
        kind = 'Bus / minibus interchange' if mini else 'Bus interchange'
    elif bus or mini:
        kind = 'Bus / minibus' if bus and mini else 'Minibus' if mini else 'Bus'
        kind += ' stopping point (on road)' if pt == 'stop_position' else ' stop'
    elif pt == 'station':
        kind = 'Transit station'
    elif pt == 'platform':
        kind = 'Boarding platform'
    elif tags.get('landuse') == 'residential':
        kind = 'Residential area'
    elif tags.get('highway'):
        kind = 'Walking path' if tags['highway'] in ('footway', 'path', 'pedestrian', 'steps') else 'Road segment'
    else:
        kind = next((tags[k].replace('_', ' ').capitalize() for k in ('amenity', 'tourism', 'shop', 'place', 'railway') if tags.get(k)), None)
        kind = kind or ('Building' if tags.get('building') else 'Mapped area' if feature_type == 'way' else 'Place')
    refs = joined(tags.get('local_ref'), tags.get('ref'))
    operator = tags.get('network') or tags.get('operator:en') or tags.get('operator')
    details = joined(operator, ('Ref ' + refs) if refs else '',
                     ('Routes ' + tags['route_ref'].replace(';', ', ')) if tags.get('route_ref') else '')
    street = ' '.join(filter(None, [tags.get('addr:housenumber'), tags.get('addr:street') or tags.get('addr:street:en')]))
    address = tags.get('addr:full') or joined(street, tags.get('addr:place'), tags.get('addr:suburb'), tags.get('addr:district'), tags.get('addr:city'))
    address = joined(address, ('Unit ' + tags['addr:unit']) if tags.get('addr:unit') else '')
    details = joined(details, ('Level ' + tags['level']) if tags.get('level') else '')
    return kind, details, address


class NearbyPlaces:
    """Nearby LandsD landmarks are context, never an asserted address/containment."""
    CELL = .005

    def __init__(self, path):
        self.cells = defaultdict(list)
        if not path.exists():
            return
        for feature in json.loads(path.read_text())['features']:
            p, geom = feature['properties'], feature.get('geometry') or {}
            if geom.get('type') != 'Point':
                continue
            # Avoid toilets, bins, parking meters, access markers as branch context.
            if p.get('TYPE') not in {'MAL', 'RSN', 'LRA', 'BUS', 'MIN'}:
                continue
            name = joined(p.get('ENGLISHNAME'), p.get('CHINESENAME'))
            if not name:
                continue
            lon, lat = geom['coordinates'][:2]
            area = joined(p.get('E_AREA'), p.get('C_AREA'))
            self.cells[self.cell(lat, lon)].append((lat, lon, name, area, str(p.get('GEONAMEID', ''))))

    def cell(self, lat, lon):
        return math.floor(lat / self.CELL), math.floor(lon / self.CELL)

    def describe(self, lat, lon):
        y, x = self.cell(lat, lon)
        closest, distance = None, 600
        for dy in (-2, -1, 0, 1, 2):
            for dx in (-2, -1, 0, 1, 2):
                for item in self.cells.get((y + dy, x + dx), ()):
                    d = math.hypot((item[0] - lat) * 111195, (item[1] - lon) * 111195 * math.cos(math.radians(lat)))
                    if d < distance:
                        closest, distance = item, d
        if closest is None:
            return '', ''
        # Approximate straight-line distance is explicitly distinguished from routing.
        return f'Near {joined(closest[2], closest[3])} (~{max(10, round(distance / 10) * 10)} m)', closest[4]


def fingerprint(paths):
    return {'schema': SCHEMA, 'inputs': {str(p): [p.stat().st_size, p.stat().st_mtime_ns] if p.exists() else None for p in paths}}


def build_places(root, cache):
    import osmium
    feed = root / 'data/generated/hk-transit-EXPERIMENTAL.gtfs.zip'
    osm = root / 'data/raw/2026-09-17/osm/hong-kong-latest.osm.pbf'
    landsd = root / 'data/landsd/places.geojson'
    signature = fingerprint([feed, osm, landsd])
    if cache.exists():
        saved = json.loads(cache.read_text())
        if isinstance(saved, dict) and saved.get('signature') == signature:
            return saved['places']
    print('Building place descriptions from OSM, transit stops and LandsD landmarks…', flush=True)
    context = NearbyPlaces(landsd)
    places = []

    def add(name, lat, lon, kind, aliases, identity, source, details='', address=''):
        if not name or not (22.08 <= lat <= 22.60 and 113.8 <= lon <= 114.5):
            return
        nearby, nearby_id = context.describe(lat, lon)
        searchable = joined(name, aliases, address, nearby).casefold()
        places.append(dict(name=name, lat=lat, lon=lon, kind=kind, details=details,
                           address=address, nearby=nearby, nearbyId=nearby_id,
                           id=identity, source=source, search=searchable,
                           searchCompact=re.sub(r'\s+', '', searchable)))

    with zipfile.ZipFile(feed) as z:
        def rows(name):
            return csv.DictReader(io.TextIOWrapper(z.open(name), encoding='utf-8-sig'))
        route_types = {r['route_id']: r['route_type'] for r in rows('routes.txt')}
        trip_types = {r['trip_id']: route_types.get(r['route_id'], '') for r in rows('trips.txt')}
        stop_types = defaultdict(set)
        for r in rows('stop_times.txt'):
            stop_types[r['stop_id']].add(trip_types.get(r['trip_id'], ''))
        modes = {'0': 'Tram / light rail', '1': 'Rail', '2': 'Rail', '3': 'Bus / minibus', '4': 'Ferry', '7': 'Funicular'}
        for r in rows('stops.txt'):
            # Preserve existing search scope; child platforms/entrances weren't indexed.
            if r.get('parent_station'):
                continue
            sid = r['stop_id']
            kind = 'MTR station' if sid.startswith('RAIL:MTR:') else 'Light rail station' if sid.startswith(('RAIL:LR:', 'RAIL:LIGHT_RAIL:')) else joined(*(modes[t] for t in sorted(stop_types[sid]) if t in modes))
            kind = kind or ('Station' if r.get('location_type') == '1' else 'Transit stop')
            if kind not in ('MTR station', 'Light rail station', 'Station', 'Transit stop'):
                kind += ' stop'
            add(r['stop_name'], float(r['stop_lat']), float(r['stop_lon']), kind, '',
                'gtfs:' + sid, 'GTFS', address=r.get('stop_desc', ''))

    class Names(osmium.SimpleHandler):
        def read(self, tags, lat, lon, feature_type, identity):
            name = tags.get('name:en') or tags.get('name')
            if not name:
                return
            zh = tags.get('name:zh') or tags.get('name:zh-Hant', '')
            name += ' · ' + zh if zh and zh not in name else ''
            kind, details, address = osm_description(tags, feature_type)
            aliases = ' '.join(v for k, v in tags.items() if k.startswith(('name', 'alt_name', 'brand', 'addr:')))
            add(name, lat, lon, kind, aliases, f'osm:{feature_type}:{identity}', 'OpenStreetMap', details, address)

        def node(self, n):
            self.read(dict(n.tags), n.location.lat, n.location.lon, 'node', n.id)

        def way(self, w):
            tags = dict(w.tags)
            if not (tags.get('name') or tags.get('name:en')):
                return
            pts = [(n.lat, n.lon) for n in w.nodes if n.location.valid()]
            if pts:
                self.read(tags, sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts), 'way', w.id)

    Names().apply_file(str(osm), locations=True)
    # No merging by name/coordinate: a station, platform and estate can coincide.
    temporary = cache.with_suffix('.tmp')
    temporary.write_text(json.dumps({'signature': signature, 'places': places}, ensure_ascii=False))
    temporary.replace(cache)
    print(f'Indexed {len(places):,} individually described places.', flush=True)
    return places
