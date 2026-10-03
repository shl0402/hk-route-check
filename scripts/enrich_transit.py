#!/usr/bin/env python3
"""Fetch, verify and compile reproducible bus/minibus source enrichment."""
import argparse
from datetime import date
import json
from pathlib import Path
from transit_enrichment import identity, official, history, holidays, published
from transit_enrichment.merge import compile_base, compile_timing
from transit_enrichment.check import verify


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['fetch','base','timing','verify'])
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--offline',action='store_true');p.add_argument('--refresh',action='store_true')
    p.add_argument('--start',type=date.fromisoformat,default=date.today())
    p.add_argument('--end',type=date.fromisoformat)
    p.add_argument('--input',type=Path);p.add_argument('--output',type=Path);p.add_argument('--anchors',type=Path)
    a=p.parse_args(); gen=a.root/'data/generated'
    if a.command=='fetch':
        for label,module in [('Route identities',identity),('Government holidays',holidays),('Published route metadata',published),('Official operator routes and stops',official),('ETA-derived timing snapshots',history)]:
            print(label,flush=True);module.fetch(a.root,offline=a.offline,refresh=a.refresh)
        print('All transit source hashes verified.',flush=True)
    elif a.command=='base':
        if not a.end:p.error('--end required for official calendar compilation')
        compile_base(a.root,a.input or gen/'hk-transit-INDOOR.gtfs.zip',a.output or gen/'hk-transit-ENRICHED-BASE.gtfs.zip',a.start,a.end)
    elif a.command=='timing':
        compile_timing(a.root,a.anchors or gen/'hk-transit-ENRICHED-BASE.gtfs.zip',a.input or gen/'hk-transit-SURFACE.gtfs.zip',a.output or gen/'hk-transit-ENRICHED.gtfs.zip')
    else:
        print(json.dumps(verify(a.anchors or gen/'hk-transit-ENRICHED-BASE.gtfs.zip',a.input or gen/'hk-transit-SURFACE.gtfs.zip',a.output or gen/'hk-transit-ENRICHED.gtfs.zip',a.root/'data/transit_enrichment/validation.json')),flush=True)

if __name__=='__main__':main()
