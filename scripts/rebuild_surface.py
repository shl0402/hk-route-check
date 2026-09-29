#!/usr/bin/env python3
"""Rebuild a surface-timing candidate in the standalone w8g server, fully offline.

No running server is started/stopped; no active routing files are overwritten.
The bundled base already contains the independently reproducible indoor update.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import surface_timing
from check_surface_feed import check


def build(root, refresh=False):
	base = root / 'data/surface-source/hk-transit-INDOOR.gtfs.zip'
	proof = json.loads((base.parent / 'manifest.json').read_text())
	if surface_timing.sha(base) != proof['sha256']:
		raise ValueError('Bundled pre-surface GTFS does not match manifest')
	surface_timing.fetch(root, offline=not refresh, refresh=refresh)
	work = root / 'data/surface-rebuild'
	shutil.copytree(root / 'data/surface/raw', work / 'data/surface/raw', dirs_exist_ok=True)
	gen = work / 'data/generated'
	gen.mkdir(parents=True, exist_ok=True)
	feed = gen / 'hk-transit-EXPERIMENTAL.gtfs.zip'
	surface_timing.merge(work, base, feed)
	check(base, feed)
	validator = root / 'tools/gtfs-validator-8.0.1-cli.jar'
	with (gen / 'validator.log').open('w') as log:
		subprocess.run(['java', '-Xmx3G', '-jar', str(validator), '-i', str(feed),
			'-o', str(gen / 'validator'), '-c', 'HK', '-t', '2', '-svu'], stdout=log, stderr=subprocess.STDOUT, check=True)
	validation = json.loads((gen / 'validator/report.json').read_text())
	if any(n['severity'] == 'ERROR' for n in validation['notices']):
		raise ValueError('GTFS validation errors; active files unchanged')
	if json.loads((gen / 'validator/system_errors.json').read_text()).get('notices'):
		raise ValueError('Validator system errors; active files unchanged')
	graph = gen / 'otp-smoke'
	graph.mkdir(exist_ok=True)
	shutil.copy2(feed, graph / 'hk.gtfs.zip')
	for name in ('hong-kong.osm.pbf', 'build-config.json', 'router-config.json'):
		shutil.copy2(root / 'data/generated/otp-smoke' / name, graph / name)
	jar = root / 'data/user_inputs/otp/otp-shaded-2.9.0.jar'
	with (gen / 'otp-build.log').open('w') as log:
		subprocess.run(['java', '-Xmx4G', '-jar', str(jar), '--build', '--save', str(graph)],
			stdout=log, stderr=subprocess.STDOUT, check=True)
	surface_timing.save(gen / 'candidate-manifest.json', dict(base_sha256=proof['sha256'],
		gtfs_sha256=surface_timing.sha(feed), graph_sha256=surface_timing.sha(graph / 'graph.obj'),
		validation_errors=0, surface_report_sha256=surface_timing.sha(work / 'data/surface/report.json')))
	print('Validated candidate:', work)
	print('Active server unchanged. Run route regressions before installation.')


if __name__ == '__main__':
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
	parser.add_argument('--refresh', action='store_true')
	args = parser.parse_args()
	build(args.root.resolve(), args.refresh)
