"""On-demand official arrival predictions for a verified boarding-stop visit.

Only the server's verified crosswalk may supply match/stop_visit. This module
does not change routing, trip ranking, a planned departure, or static GTFS data.
"""
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import json
import math
import re
import threading
import time

HKT = timezone(timedelta(hours=8))
CACHE_SECONDS = 15
NETWORK_SECONDS = 8
MAX_GENERATED_AGE = 120
MAX_RECORD_AGE = 300
MAX_PREDICTION_AHEAD = 3 * 3600
MAX_BODY_BYTES = 512_000
MAX_CACHE_ENTRIES = 256
BASES = {
    'KMB': 'https://data.etabus.gov.hk/v1/transport/kmb',
    'CTB': 'https://rt.data.gov.hk/v2/transport/citybus',
    'GMB': 'https://data.etagmb.gov.hk',
}
NAMES = {'KMB': 'KMB official arrivals', 'CTB': 'Citybus official arrivals',
         'GMB': 'Green minibus official arrivals'}
_cache = OrderedDict()
_lock = threading.Lock()
_requests = threading.BoundedSemaphore(4)


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            return None
        return result.astimezone(HKT)
    except (ValueError, OverflowError):
        return None


def _number(value, high=500):
    if isinstance(value, bool) or not re.fullmatch(r'[1-9][0-9]*', str(value)):
        raise ValueError('Missing verified service or stop sequence')
    number = int(value)
    if number > high:
        raise ValueError('Service or stop sequence is out of range')
    return number


def _identity(match, visit):
    if not isinstance(match, dict) or not isinstance(visit, dict):
        raise ValueError('A verified route and stop visit are required')
    if match.get('evidence') != 'official_full_stop_sequence':
        raise ValueError('Live arrivals require an official full stop-sequence match')
    operator = match.get('operator')
    if operator not in BASES:
        raise ValueError('Live arrivals are unavailable for this operator')
    route = str(match.get('route') or '').upper()
    if not re.fullmatch(r'[A-Z0-9]{1,12}', route):
        raise ValueError('Invalid verified route code')
    direction = str(match.get('direction') or '')
    seq = _number(visit.get('seq'))
    gtfs_seq = seq
    stop_id = str(visit.get('native_stop_id') or '')
    # A proven circular pattern can join overlapping official directions.
    # Its GTFS position is not the operator API's stop occurrence number.
    override_fields = ('official_direction', 'official_sequence', 'official_pattern_key')
    has_override = any(visit.get(field) is not None for field in override_fields)
    if has_override:
        if not all(visit.get(field) is not None for field in override_fields):
            raise ValueError('Incomplete operator stop-occurrence identity')
        component = visit['official_pattern_key']
        components = match.get('official_pattern_keys') or [match.get('official_pattern_key')]
        if not isinstance(components, list) or component not in components:
            raise ValueError('Operator stop occurrence is outside the verified route components')
        direction = str(visit['official_direction'])
        seq = _number(visit['official_sequence'])
        if operator == 'CTB' and component != f'CTB:{route}:{direction}':
            raise ValueError('Citybus component and direction disagree')
        if 'stop_visits' not in match:
            raise ValueError('Operator occurrence overrides require the verified full crosswalk')
    # No supplied URL is read. Values used in paths have strict identity syntax.
    if operator == 'KMB':
        if direction not in ('O', 'I') or not re.fullmatch(r'[0-9A-F]{16}', stop_id):
            raise ValueError('Invalid KMB direction or stop identity')
        service = str(_number(match.get('service_type'), 999))
        url = f'{BASES[operator]}/eta/{stop_id}/{route}/{service}'
        native_route = None
    elif operator == 'CTB':
        if direction not in ('O', 'I') or not re.fullmatch(r'[0-9]{6}', stop_id):
            raise ValueError('Invalid Citybus direction or stop identity')
        service = None
        native_route = None
        url = f'{BASES[operator]}/eta/CTB/{stop_id}/{route}'
    else:
        if direction not in ('1', '2') or not re.fullmatch(r'[0-9]{1,10}', stop_id):
            raise ValueError('Invalid minibus route direction or stop identity')
        native_route = str(_number(match.get('native_route_id'), 9_999_999_999))
        service = None
        url = f'{BASES[operator]}/eta/route-stop/{native_route}/{direction}/{seq}'
    # If the full crosswalk accompanies the call, the selected visit must occur
    # there at the same index; repeated stop IDs alone cannot identify a visit.
    if 'stop_visits' in match:
        if not any(str(v.get('native_stop_id')) == stop_id and str(v.get('seq')) == str(gtfs_seq)
                   and (not has_override or all(v.get(field) == visit.get(field) for field in override_fields))
                   for v in match['stop_visits'] if isinstance(v, dict)):
            raise ValueError('Boarding visit is not in the verified route pattern')
    return dict(operator=operator, route=route, direction=direction, service_type=service,
                native_route_id=native_route, native_stop_id=stop_id, seq=seq,
                gtfs_seq=gtfs_seq, url=url)


def _request_json(url):
    import requests
    from urllib3.util import Timeout
    started = time.monotonic()
    # No redirects, cookies, token or user-selected host. No automatic retries.
    with requests.get(url, timeout=Timeout(total=NETWORK_SECONDS, connect=3, read=5),
                      allow_redirects=False, stream=True,
                      headers={'User-Agent': 'map-routing-official-arrivals/1.0'}) as response:
        if response.status_code != 200:
            raise ValueError('Official arrival service is unavailable')
        parts, size = [], 0
        for part in response.iter_content(1024):
            if time.monotonic() - started > NETWORK_SECONDS:
                raise TimeoutError('Arrival request exceeded its time budget')
            size += len(part)
            if size > MAX_BODY_BYTES:
                raise ValueError('Arrival response exceeds the size limit')
            parts.append(part)
        result = json.loads(b''.join(parts))
        if not isinstance(result, dict):
            raise ValueError('Unexpected arrival response')
        return result


def _empty(status, now, message, identity=None, fetched_at=None):
    return dict(status=status, departures=[], label='Official arrivals now',
                source=dict(name=NAMES[identity['operator']], url=identity['url']) if identity else None,
                fetched_at=fetched_at or now.isoformat(), generated_at=None,
                cached=False, warnings=[message], timezone='Asia/Hong_Kong')


def _same_number(value, expected):
    try:
        return _number(value, 9_999_999_999) == int(expected)
    except (ValueError, TypeError):
        return False


def normalize_response(payload, identity, *, now, fetched_at=None):
    """Public for deterministic tests; discard stale or mismatched predictions."""
    now = now.astimezone(HKT)
    response = _empty('unavailable', now, 'No upcoming predictions for this stop visit.',
                      identity, fetched_at)
    if not isinstance(payload, dict):
        return response
    generated = _timestamp(payload.get('generated_timestamp'))
    if not generated or not -60 <= (now - generated).total_seconds() <= MAX_GENERATED_AGE:
        response.update(status='stale', warnings=['Official arrival response is stale or its timestamp is missing.'])
        return response
    response['generated_at'] = generated.isoformat()
    operator = identity['operator']
    data = payload.get('data')
    warnings = []
    if operator == 'GMB':
        if not isinstance(data, dict) or str(data.get('stop_id')) != identity['native_stop_id']:
            response['warnings'] = ['Official response does not match the verified boarding stop.']
            return response
        if data.get('enabled') is not True:
            response['warnings'] = [str(data.get('description_en') or 'Arrival prediction is disabled for this stop.')[:240]]
            return response
        records = data.get('eta')
        if not isinstance(records, list):
            return response
    else:
        if not isinstance(data, list):
            return response
        records = []
        for row in data:
            if not isinstance(row, dict):
                continue
            if row.get('route') != identity['route'] or row.get('dir') != identity['direction']:
                continue
            if not _same_number(row.get('seq'), identity['seq']):
                continue
            if operator == 'KMB' and not _same_number(row.get('service_type'), identity['service_type']):
                continue
            if 'co' in row and row['co'] != operator:
                continue
            if 'stop' in row and str(row['stop']) != identity['native_stop_id']:
                continue
            records.append(row)
        if operator == 'CTB':
            warnings.append('Citybus does not identify service variants in its arrival response.')
    accepted, stale = [], 0
    for record in records:
        if not isinstance(record, dict):
            continue
        if operator != 'GMB':
            updated = _timestamp(record.get('data_timestamp'))
            if not updated or not -60 <= (now - updated).total_seconds() <= MAX_RECORD_AGE:
                stale += 1
                continue
        else:
            updated = generated
        arrival = _timestamp(record.get('timestamp' if operator == 'GMB' else 'eta'))
        if arrival is None:
            continue
        remaining = (arrival - now).total_seconds()
        if not -30 <= remaining <= MAX_PREDICTION_AHEAD:
            continue
        remarks = {lang: str(record.get(('remarks_' if operator == 'GMB' else 'rmk_') + lang) or '')[:240]
                   for lang in ('en', 'tc')}
        text = remarks['en'] + ' ' + remarks['tc']
        kind = 'scheduled' if re.search(r'scheduled|預定|未開出', text, re.I) else 'prediction'
        accepted.append(dict(time=arrival.isoformat(), clock=arrival.strftime('%H:%M'),
                             minutes=max(0, math.ceil(remaining / 60)), kind=kind,
                             remarks=remarks, updated_at=updated.isoformat()))
    accepted.sort(key=lambda item: item['time'])
    # ETA sequence means prediction ordering, never a shared vehicle identifier.
    unique = {(r['time'], r['kind']): r for r in accepted}
    departures = list(unique.values())[:3]
    if stale:
        warnings.append('Stale prediction records were omitted.')
    if departures:
        response.update(status='ok', departures=departures, warnings=warnings)
    elif stale:
        response.update(status='stale', warnings=warnings)
    else:
        response['warnings'] += warnings
    return response


def fetch_arrivals(match, stop_visit):
    """Fetch current predictions with a 15-second bounded in-memory cache.

    Both arguments must come from the server's loaded verified crosswalk, never
    from a user-provided arbitrary match object. Predictions describe *now*, not
    the departure date of a previously planned itinerary.
    """
    now = datetime.now(HKT)
    try:
        identity = _identity(match, stop_visit)
    except (ValueError, TypeError):
        return _empty('invalid_identity', now, 'A verified operator route and boarding-stop visit are required.')
    key = tuple(identity[k] for k in ('operator', 'route', 'direction', 'service_type',
                                     'native_route_id', 'native_stop_id', 'seq'))
    cached = False
    with _lock:
        entry = _cache.get(key)
        if entry and time.monotonic() - entry['created'] < CACHE_SECONDS:
            _cache.move_to_end(key)
            cached = True
    if not cached:
        if not _requests.acquire(blocking=False):
            return _empty('unavailable', now, 'Arrival service is busy. Try again shortly.', identity)
        try:
            try:
                payload = _request_json(identity['url'])
                error = None
            except Exception:
                payload, error = None, 'Official arrival service is unavailable. Try again shortly.'
            entry = dict(payload=payload, error=error, fetched_at=datetime.now(HKT).isoformat(),
                         created=time.monotonic())
            with _lock:
                _cache[key] = entry
                _cache.move_to_end(key)
                while len(_cache) > MAX_CACHE_ENTRIES:
                    _cache.popitem(last=False)
        finally:
            _requests.release()
    now = datetime.now(HKT)
    if entry['error']:
        result = _empty('unavailable', now, entry['error'], identity, entry['fetched_at'])
    else:
        result = normalize_response(entry['payload'], identity, now=now, fetched_at=entry['fetched_at'])
    result['cached'] = cached
    return result
