#!/usr/bin/env python3
"""Reproduce the Hemera → South Seas Centre overnight boarding-wait check."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import urllib.request


def run(url, date, output):
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for clock in ('03:27', '03:35'):
        body = dict(origin=dict(lat=22.29693, lon=114.27043),
            destination=dict(lat=22.29976928, lon=114.17862282),
            departure=date+'T'+clock+':00+08:00', modes=['bus'],
            preference='fastest', includeAlternatives=False)
        req = urllib.request.Request(url+'/api/route', data=json.dumps(body).encode(),
            headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req, timeout=120) as response:
            data=json.load(response)
        (output/('n796-'+clock.replace(':','')+'.json')).write_text(
            json.dumps(dict(request=body,response=data),ensure_ascii=False,indent=2))
        assert data['itineraries'], data
        it=data['itineraries'][0]
        rides=[l for l in it['legs'] if l['transitLeg']]
        assert len(rides)==1 and rides[0]['route']['shortName']=='N796', rides
        bus=rides[0];p=bus['provenance']
        assert p['timingKind']=='published_departure_list' and not p['feedIntervals'], p
        assert p.get('identityVerification') and p.get('corroboratingUrl'), p
        assert bus['departureBasis']=='listed'
        assert it['initialWaitSeconds']<60, 'A whole headway must not be added to a scheduled departure'
        assert it['displayDurationSeconds']==round(datetime.fromisoformat(it['end']).timestamp()-datetime.fromisoformat(body['departure']).timestamp())
        summary=dict(requested=clock,leave=it['start'],arrive=it['end'],
            total_minutes=round(it['displayDurationSeconds']/60,2),
            after_leaving_minutes=round(it['duration']/60,2),
            before_leaving_minutes=round(it['originWaitSeconds']/60,2),
            boarding_wait_seconds=it['initialWaitSeconds'],boarding=bus['start']['scheduledTime'])
        results.append(summary);print(json.dumps(summary),flush=True)
    assert results[0]['arrive']==results[1]['arrive'], 'Both ready times should catch the same scheduled trip'
    (output/'n796-summary.json').write_text(json.dumps(results,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url',default='http://127.0.0.1:8000')
    p.add_argument('--date',default='2026-10-01')
    p.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'data/routing-checks/2026-10-01')
    a=p.parse_args();run(a.url,a.date,a.output)
