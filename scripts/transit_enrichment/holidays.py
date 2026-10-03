"""Pinned government public holidays; never extrapolate beyond supplied years."""
import json
from datetime import datetime, timezone
from pathlib import Path
from .feed import save, sha

URL = 'https://www.1823.gov.hk/common/ical/en.json'
PAGE = 'https://www.1823.gov.hk/en/hong-kong-public-holidays-ical'


def fetch(root, offline=False, refresh=False):
    base = Path(root) / 'data/transit_enrichment/raw/holidays'
    path, mp = base / 'calendar.json', base / 'manifest.json'
    if path.exists() and mp.exists() and not refresh:
        meta = json.loads(mp.read_text())
        if sha(path) != meta['sha256']:
            raise ValueError('Holiday source checksum mismatch')
        return meta
    if offline:
        raise ValueError('Missing verified public holiday cache')
    import requests
    response = requests.get(URL, timeout=30)
    response.raise_for_status()
    json.loads(response.content.decode('utf-8-sig'))['vcalendar'][0]['vevent']
    base.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix('.part'); part.write_bytes(response.content); part.replace(path)
    meta = dict(url=URL, page=PAGE, sha256=sha(path), retrieved_at=datetime.now(timezone.utc).isoformat())
    save(mp, meta)
    return meta


def load(root):
    meta = fetch(root, offline=True)
    raw = json.loads((Path(root) / 'data/transit_enrichment/raw/holidays/calendar.json').read_text(encoding='utf-8-sig'))
    dates = sorted({e['dtstart'][0] for e in raw['vcalendar'][0]['vevent']})
    for d in dates:
        datetime.strptime(d, '%Y%m%d')
    years = sorted({int(d[:4]) for d in dates})
    # Require a complete-looking government calendar, including New Year's Day
    # and Christmas; never treat an arbitrary first/last event as full coverage.
    if not years or any(f'{y}0101' not in dates or f'{y}1225' not in dates or sum(d.startswith(str(y)) for d in dates) < 15 for y in years):
        raise ValueError('Incomplete public holiday calendar')
    return dict(dates=dates, years=years, source=meta)
