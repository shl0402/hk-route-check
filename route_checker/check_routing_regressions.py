#!/usr/bin/env python3
"""Exercise ranking, whole-MTR alternatives, ferries and rural map coverage.

Run against the rebuilt server, e.g. --url http://127.0.0.1:8100.
Evidence is saved after each request so an interrupted run remains inspectable.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import time
import urllib.request
import route_selection

ALL=['mtr','bus','ferry','light_rail','tram','funicular']
CASES=[
    ('lohas_southseas',(22.2973,114.272),(22.29976928,114.17862282),ALL,'fastest'),
    ('lohas_less_walking',(22.2973,114.272),(22.29976928,114.17862282),['mtr'],'walking'),
    ('central_cheungchau',(22.2825,114.1555),(22.2095,114.0318),['ferry'],'fastest'),
    ('central_muiwo',(22.2825,114.1555),(22.2651,114.0024),['ferry'],'fastest'),
    ('central_sokkwuwan',(22.2825,114.1555),(22.2054,114.1326),['ferry'],'fastest'),
    ('tinshui_tuenmun',(22.4565,113.9984),(22.3905,113.978),['light_rail','bus'],'fastest'),
    ('rural_paktam',(22.3974,114.323),(22.3191,114.1705),ALL,'fastest'),
    ('short_walk',(22.2825,114.1555),(22.2852,114.1518),[],'fastest'),
]

def run(url,out,date):
    out.mkdir(parents=True,exist_ok=True);summary=[]
    for name,a,b,modes,preference in CASES:
        request=dict(origin=dict(zip(('lat','lon'),a)),destination=dict(zip(('lat','lon'),b)),
            departure=date+'T17:00:00+08:00',modes=modes,preference=preference,includeAlternatives=True,maxResults=6)
        started=time.monotonic()
        req=urllib.request.Request(url+'/api/route',data=json.dumps(request).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=180) as response:result=json.load(response)
        (out/(name+'.json')).write_text(json.dumps(dict(request=request,response=result),ensure_ascii=False,indent=2))
        items=result['itineraries'];assert items, name+': no route'
        stamp=lambda x:datetime.fromisoformat(x).timestamp()
        keys=[route_selection.rank_key(i,preference,stamp) for i in items]
        assert keys==sorted(keys),name+': incorrect ranking'
        assert len({route_selection.signature(i) for i in items})==len(items),name+': duplicate route'
        for it in items:
            assert it['displayDurationSeconds']==round(stamp(it['end'])-stamp(request['departure'])),name+': missing wait'
            assert it['initialWaitExcludedSeconds']==0
            for leg in it['legs']:
                if leg.get('provenance',{}).get('journeyTiming'):
                    assert abs(leg['duration']-leg['provenance']['journeyTiming']['seconds'])<=1,name+': MTR total altered'
        if name=='lohas_southseas':
            exits={route_selection.station_key(l['to'].get('stop',{}).get('gtfsId')) for i in items for l in i['legs'] if ':RAIL:MTR:' in (l.get('route') or {}).get('gtfsId','')}
            assert len(exits)>=2,'MTR exit alternatives missing'
        if name in ('central_cheungchau','central_muiwo'):
            ferries=[l for it in items for l in it['legs'] if l['mode']=='FERRY']
            assert all(l['provenance']['sourceType']=='operator' for l in ferries)
            assert any(l['duration']==2400 for l in ferries),'Expected published fast-ferry planning allowance'
            assert all(l['duration']==max(l['provenance']['operatorTiming']['duration_range_seconds']) for l in ferries)
        if name=='central_sokkwuwan':assert result['sources']['search']['windowSeconds']>=7200
        row=dict(case=name,options=len(items),seconds=round(time.monotonic()-started,2),
            first_arrival=items[0]['end'],elapsed_minutes=round(items[0]['displayDurationSeconds']/60,2),
            access_warnings=sum(len(i.get('accessGaps',[])) for i in items),warnings=result['warnings'])
        summary.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
        (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--url',default='http://127.0.0.1:8000')
    p.add_argument('--date',default='2026-09-30');p.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'data/routing-checks/2026-09-30')
    a=p.parse_args();run(a.url,a.output,a.date)
