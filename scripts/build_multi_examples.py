#!/usr/bin/env python3
"""Extend the original 20 sample places to 100 from the local OSM place index.

No invented coordinates. Deterministic round-robin selection near the original
anchors keeps urban and rural examples; a 150 m separation avoids duplicates.
Run after the normal place-index build. The original first 20 remain unchanged.
"""
import argparse
import json
import math
from pathlib import Path

def distance(a,b):
    y=math.radians(a['lat']-b['lat']);x=math.radians(a['lon']-b['lon'])*math.cos(math.radians((a['lat']+b['lat'])/2))
    return math.hypot(x,y)*6371000

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    file=args.output or args.root/'route_checker/multi_examples.json'
    anchors=json.loads(file.read_text())[:20]
    places=json.loads((args.root/'route_checker/places.json').read_text())['places']
    candidates=[p for p in places if p.get('source')=='OpenStreetMap' and p.get('kind') in ('Restaurant','Cafe') and p.get('id','').startswith('osm:')]
    nearest=[sorted(candidates,key=lambda p:(distance(a,p),p['id'])) for a in anchors]
    chosen=list(anchors);used={p['sourceId'] for p in anchors}
    for _ in range(4):
        for options in nearest:
            p=next((p for p in options if p['id'] not in used and all(distance(p,q)>=150 for q in chosen)),None)
            if p is None:raise RuntimeError('Insufficient distinct sourced places for 100 samples.')
            used.add(p['id']);_,kind,ident=p['id'].split(':')
            chosen.append({k:p.get(k,'') for k in ('name','lat','lon','kind','address','nearby')} | dict(sourceId=p['id'],sourceUrl=f'https://www.openstreetmap.org/{kind}/{ident}',snapshot=anchors[0]['snapshot']))
    file.write_text(json.dumps(chosen,ensure_ascii=False,indent=2)+'\n')
    print(f'{len(chosen)} sourced sample places written to {file}')
if __name__=='__main__':main()
