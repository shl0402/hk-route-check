#!/usr/bin/env python3
"""Build an isolated indoor routing candidate from a bundled base feed.

For the embedded w8g server; does not start/stop servers or overwrite an active
graph. For the complete upstream timetable rebuild, use map_routing/run.py build.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

import indoor_network
import landsd_enrich
import surface_timing
import operator_sources
import operator_timing
from check_surface_feed import check
from indoor_timing import write_otp_config


def build(root, refresh=False):
	base = root / 'data/indoor-source/hk-transit-LANDSD.gtfs.zip'
	proof = json.loads((base.parent / 'manifest.json').read_text())
	if landsd_enrich.sha(base) != proof['sha256']:
		raise ValueError('Bundled pre-indoor GTFS does not match its manifest')
	if refresh:
		landsd_enrich.fetch(root, refresh=True)
		indoor_network.fetch(root, refresh=True)
		indoor_network.fetch_original(root, refresh=True)
		surface_timing.fetch(root, refresh=True)
	operator_sources.fetch(root, offline=not refresh, refresh=refresh)
	work = root / 'data/indoor-rebuild'
	shutil.copytree(root / 'data/landsd/raw', work / 'data/landsd/raw', dirs_exist_ok=True)
	(work / 'data/mtr_api/raw').mkdir(parents=True, exist_ok=True)
	shutil.copy2(root / 'data/mtr_api/inventory.json', work / 'data/mtr_api/inventory.json')
	for source in (root / 'data/mtr_api/raw').glob('HR_*.json'):
		shutil.copy2(source, work / 'data/mtr_api/raw' / source.name)
	gen = work / 'data/generated'
	gen.mkdir(parents=True, exist_ok=True)
	feed = gen / 'hk-transit-EXPERIMENTAL.gtfs.zip'
	refreshed_base = gen / 'hk-transit-LANDSD.gtfs.zip'
	landsd_enrich.merge(work, base, refreshed_base)
	indoor_feed = gen / 'hk-transit-INDOOR.gtfs.zip'
	indoor_network.compile_data(work, refreshed_base, indoor_feed)
	shutil.copytree(root / 'data/surface/raw', work / 'data/surface/raw', dirs_exist_ok=True)
	shutil.copytree(root / 'data/operators/raw', work / 'data/operators/raw', dirs_exist_ok=True)
	surface_feed = gen / 'hk-transit-SURFACE.gtfs.zip'
	surface_timing.merge(work, indoor_feed, surface_feed)
	check(indoor_feed, surface_feed)
	operator_timing.merge(work, surface_feed, feed)
	validator = root / 'tools/gtfs-validator-8.0.1-cli.jar'
	if not validator.exists():
		raise ValueError('Missing pinned validator JAR; restore it from the routing release')
	with (gen / 'validator.log').open('w') as log:
		subprocess.run(['java', '-Xmx3G', '-jar', str(validator), '-i', str(feed),
			'-o', str(gen / 'validator'), '-c', 'HK', '-t', '2', '-svu'], stdout=log, stderr=subprocess.STDOUT, check=True)
	validation = json.loads((gen / 'validator/report.json').read_text())
	if any(n['severity'] == 'ERROR' for n in validation['notices']):
		raise ValueError('GTFS validation failed; active server not modified')
	if json.loads((gen / 'validator/system_errors.json').read_text()).get('notices'):
		raise ValueError('Validator system errors; active server not modified')
	graph = gen / 'otp-smoke'
	graph.mkdir(exist_ok=True)
	shutil.copy2(feed, graph / 'hk.gtfs.zip')
	for name in ('hong-kong.osm.pbf', 'build-config.json'):
		shutil.copy2(root / 'data/generated/otp-smoke' / name, graph / name)
	write_otp_config(graph)
	jar = root / 'data/user_inputs/otp/otp-shaded-2.9.0.jar'
	with (gen / 'otp-build.log').open('w') as log:
		subprocess.run(['java', '-Xmx4G', '-jar', str(jar), '--build', '--save', str(graph)],
			stdout=log, stderr=subprocess.STDOUT, check=True)
	manifest = dict(base_feed_sha256=proof['sha256'], gtfs_sha256=landsd_enrich.sha(feed),
		graph_sha256=landsd_enrich.sha(graph / 'graph.obj'), validation_errors=0,
		router_config_sha256=landsd_enrich.sha(graph / 'router-config.json'),
		build_config_sha256=landsd_enrich.sha(graph / 'build-config.json'),
		indoor_validation_sha256=landsd_enrich.sha(work / 'data/landsd/indoor/validation.json'))
	landsd_enrich.save(gen / 'candidate-manifest.json', manifest)
	print('Validated candidate:', work)
	print('Active graph unchanged. Run routing regressions against this candidate before installation.')


if __name__ == '__main__':
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
	parser.add_argument('--refresh', action='store_true', help='Refresh public source datasets; otherwise fully offline')
	args = parser.parse_args()
	build(args.root.resolve(), args.refresh)
