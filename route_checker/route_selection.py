"""Distinct door-to-door candidates, ranked against one requested departure.

Station distance only bounds candidate discovery. OTP computes every complete
walk/transit journey; no guessed access time is inserted into an itinerary.
"""
import copy
import math
import re


def station_key(value):
    match = re.search(r'(?:^|:)RAIL:MTR:(\d+)(?::|$)', value or '')
    return match.group(1) if match else None


def distance_m(point, stop):
    a, b = math.radians(point['lat']), math.radians(float(stop['stop_lat']))
    dx = math.radians(point['lon'] - float(stop['stop_lon']))
    h = math.sin((a-b)/2)**2 + math.cos(a)*math.cos(b)*math.sin(dx/2)**2
    return 12742000 * math.asin(min(1, math.sqrt(h)))


def nearby_station_groups(point, stops, engine_ids, limit=6, radius=2500):
    groups = {}
    for scoped in engine_ids:
        sid = scoped.partition(':')[2]
        key = station_key(sid)
        stop = stops.get(sid)
        if key is None or not stop or stop.get('location_type', '0') not in ('', '0'):
            continue
        distance = distance_m(point, stop)
        if distance > radius:
            continue
        group = groups.setdefault(key, dict(station=key, stops=[], distance=distance))
        group['stops'].append(scoped)
        group['distance'] = min(group['distance'], distance)
    return sorted(groups.values(), key=lambda g: (g['distance'], g['station']))[:limit]


def recovery_requests(variables, origins, destinations, limit=16):
    """Constrain actual boarding/alighting stations, not synthetic duration IDs.

    Endpoint constraints recover Pareto-pruned station alternatives. A bounded
    pair pass also explores routes that change both boarding and exit station.
    This is a candidate search, not a claim to enumerate every feasible journey.
    """
    common = copy.deepcopy(variables)
    common['modes']['transit']['transit'] = [{'mode': 'SUBWAY'}, {'mode': 'RAIL'}]
    common['prefs']['transit'].pop('filters', None)
    common['prefs']['transit']['transfer']['maximumTransfers'] = 0
    specs = [(None, d) for d in destinations] + [(o, None) for o in origins]
    specs += [(o, d) for o in origins for d in destinations if o['station'] != d['station']]
    for origin, destination in specs[:limit]:
        v = copy.deepcopy(common)
        v['via'] = [dict(passThrough=dict(stopLocationIds=g['stops']))
                    for g in (origin, destination) if g]
        yield v, (origin['station'] if origin else None,
                  destination['station'] if destination else None)


def matches_stations(itinerary, expected):
    transit = [l for l in itinerary['legs'] if l['transitLeg']]
    if len(transit) != 1:
        return False
    leg = transit[0]
    actual = tuple(station_key((leg[k].get('stop') or {}).get('gtfsId')) for k in ('from', 'to'))
    return all(want is None or got == want for got, want in zip(actual, expected))


def signature(itinerary):
    """Ignore departure copies and generated MTR duration buckets, not paths."""
    parts = []
    for leg in itinerary['legs']:
        route = (leg.get('route') or {}).get('gtfsId')
        mtr = ':RAIL:MTR:' in (route or '')
        endpoints = tuple((leg[k].get('stop') or {}).get('gtfsId') or
                          (round(leg[k]['lat'], 5), round(leg[k]['lon'], 5))
                          for k in ('from', 'to'))
        path = tuple(leg.get('mtrPathStopIds', ())) if mtr else ()
        vessel = (leg.get('provenance', {}).get('operatorTiming') or {}).get('vessel')
        parts.append((leg['mode'], 'MTR' if mtr else route, endpoints, path, vessel))
    return tuple(parts)


def rank_key(itinerary, preference, timestamp):
    arrival = timestamp(itinerary['end'])
    if preference == 'walking':
        return itinerary['walkDistance'], arrival, itinerary.get('transfers', 0)
    if preference == 'transfers':
        return itinerary.get('transfers', 0), arrival, itinerary['walkDistance']
    return arrival, itinerary['walkDistance'], itinerary.get('transfers', 0)


def decode_geometry(encoded):
    points=[];offset=lat=lon=0
    try:
        while offset<len(encoded):
            delta=[]
            for _ in range(2):
                result=shift=0
                while True:
                    value=ord(encoded[offset])-63;offset+=1
                    result|=(value&31)<<shift;shift+=5
                    if value<32:break
                    if shift>35:raise ValueError('Invalid polyline')
                delta.append(~(result>>1) if result&1 else result>>1)
            lat+=delta[0];lon+=delta[1];points.append((lat/1e5,lon/1e5))
    except (IndexError,ValueError):return []
    return points


def endpoint_coverage(itinerary,origin,destination):
    """Measure the actual rendered path gap; never add invented walking time."""
    legs=itinerary.get('legs',[])
    if not legs:return []
    gaps=[]
    for name,requested,leg,index in [('origin',origin,legs[0],0),('destination',destination,legs[-1],-1)]:
        points=decode_geometry((leg.get('legGeometry') or {}).get('points',''))
        if not points:continue
        lat,lon=points[index]
        offset=round(distance_m(requested,dict(stop_lat=lat,stop_lon=lon)))
        if offset>50:
            gaps.append(dict(endpoint=name,distanceMetres=offset,mappedPoint=dict(lat=lat,lon=lon),
                message=f'{name.title()} is {offset} m from the mapped route. The connecting walk is not verified or included in the time.'))
    return gaps


def select(items, preference, maximum, timestamp):
    seen, result = set(), []
    for item in sorted(items, key=lambda it: rank_key(it, preference, timestamp)):
        key = signature(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result[:maximum]
