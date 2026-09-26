#!/usr/bin/env python3
"""Benchmark the running local checker with sourced non-station sample jobs."""
import argparse
import json
import time
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['fast','full','large'],default='fast')
    parser.add_argument('--no-cache',action='store_true',help='Bypass OTP pair and R5 matrix results; keep prepared routing networks')
    parser.add_argument('--counts', default='4,8,20')
    parser.add_argument('--budget', type=int, default=600, help='Overall seconds per case')
    parser.add_argument('--server', default='http://127.0.0.1:8000')
    args = parser.parse_args()
    folder = Path(__file__).resolve().parents[1] / 'data/optimization/benchmarks'
    folder.mkdir(parents=True, exist_ok=True)

    def call(path, data=None):
        request = urllib.request.Request(args.server.rstrip('/') + path,
            data=None if data is None else json.dumps(data).encode(),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    for count in map(int, args.counts.split(',')):
        request = call('/api/optimization/example?count=' + str(count))
        request['maxRuntimeSeconds'] = args.budget
        request['planningMode'] = args.mode
        request['useTravelCache'] = not args.no_cache
        request['maxRounds'] = 10 if args.mode != 'full' else 3
        started = call('/api/optimizations', request)
        ident, cursor, events = started['id'], 0, []
        print(f'{count} jobs: {args.server}/multi?run={ident}', flush=True)
        while True:
            state = call('/api/optimizations/' + ident + '?after=' + str(cursor))
            for event in state['events']:
                cursor = event['seq']
                events.append(event)
                progress = f" {event['done']}/{event['total']}" if 'total' in event else ''
                print(f"{event['elapsedSeconds']:7.1f}s {event['stage']}{progress}: {event['message']}", flush=True)
            if state['status'] != 'running':
                break
            time.sleep(1)
        state['events'] = events
        path = folder / f'{count}-{ident}.json'
        path.write_text(json.dumps({'request': request, 'state': state}, ensure_ascii=False, indent=2))
        result = state.get('result') or {}
        print(json.dumps({'jobs': count, 'status': result.get('status', state['status']),
            'workers': result.get('workersUsed'), 'metrics': result.get('metrics'),
            'error': state.get('error'), 'saved': str(path)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
