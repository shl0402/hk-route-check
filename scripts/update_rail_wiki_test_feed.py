#!/usr/bin/env python3
"""Normalize cached rail wiki, validate a candidate, rebuild OTP, and activate. Restart the checker afterwards."""

import argparse, hashlib, json, shutil, subprocess, sys, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
G = ROOT / 'data/generated'
OUT = ROOT / 'data/rail_wiki'


def sha(p):
	return hashlib.sha256(p.read_bytes()).hexdigest()


def run(cmd, log=None):
	print('Running:', ' '.join(map(str, cmd)), flush=True)
	if log:
		with log.open('w') as f:
			subprocess.run(
				list(map(str, cmd)),
				cwd=ROOT,
				stdout=f,
				stderr=subprocess.STDOUT,
				check=True,
			)
	else:
		subprocess.run(list(map(str, cmd)), cwd=ROOT, check=True)


def main():
	ap = argparse.ArgumentParser(description=__doc__)
	ap.add_argument('--prepare-only', action='store_true')
	ap.add_argument('--activate-staged', action='store_true')
	a = ap.parse_args()
	candidate = G / 'hk-transit-RAIL-WIKI.gtfs.zip'
	stage = G / 'otp-rail-wiki-stage'
	live = G / 'otp-smoke'
	manifest = G / 'rail-wiki-stage.json'
	if not a.activate_staged:
		run(
			[sys.executable, ROOT / 'scripts/hkrail/normalize.py'],
			OUT / 'normalization.log',
		)
		run([sys.executable, ROOT / 'scripts/hkrail/merge_gtfs.py'])
		run([sys.executable, ROOT / 'scripts/normalize_mtr_api.py', '--root', ROOT])
		run([sys.executable, ROOT / 'scripts/apply_mtr_api.py', '--root', ROOT])
		candidate = G / 'hk-transit-MTR-API.gtfs.zip'
		run(
			[
				'java',
				'-Xmx3G',
				'-jar',
				ROOT / 'tools/gtfs-validator-8.0.1-cli.jar',
				'-i',
				candidate,
				'-o',
				G / 'rail-wiki-validator',
				'-c',
				'HK',
				'-t',
				'2',
				'-svu',
			],
			G / 'rail-wiki-validator.log',
		)
		report = json.loads((G / 'rail-wiki-validator/report.json').read_text())
		errors = json.loads((G / 'rail-wiki-validator/system_errors.json').read_text())
		if any(n['severity'] == 'ERROR' for n in report['notices']) or errors.get(
			'notices'
		):
			raise SystemExit('GTFS validation failed; active feed unchanged.')
		run([sys.executable, ROOT / 'scripts/hkrail/check_merge.py'])
		stage.mkdir(exist_ok=True)
		shutil.copy2(candidate, stage / 'hk.gtfs.zip')
		shutil.copy2(live / 'build-config.json', stage / 'build-config.json')
		if not (stage / 'hong-kong.osm.pbf').exists():
			(stage / 'hong-kong.osm.pbf').symlink_to(
				(live / 'hong-kong.osm.pbf').resolve()
			)
		run(
			[
				'java',
				'-Xmx4G',
				'-jar',
				ROOT / 'data/user_inputs/otp/otp-shaded-2.9.0.jar',
				'--build',
				'--save',
				stage,
			],
			G / 'rail-wiki-otp-build.log',
		)
		manifest.write_text(
			json.dumps(
				dict(
					candidate=candidate.name,
					feed_sha256=sha(candidate),
					graph_sha256=sha(stage / 'graph.obj'),
					validation_errors=0,
				),
				indent=2,
			)
		)
	m = json.loads(manifest.read_text())
	candidate = G / m.get('candidate', 'hk-transit-RAIL-WIKI.gtfs.zip')
	if (
		m['feed_sha256'] != sha(candidate)
		or sha(stage / 'hk.gtfs.zip') != sha(candidate)
		or m['graph_sha256'] != sha(stage / 'graph.obj')
	):
		raise SystemExit('Staged files changed; rebuild required.')
	with zipfile.ZipFile(candidate) as z:
		proof = json.loads(z.read('mtr_api_provenance.json'))
		if (
			proof.get('mtr_distance_fallbacks') != 0
			or not proof.get('mtr_trip_templates')
			or not proof.get('od_connections')
		):
			raise SystemExit(
				'Rebuild required: staged MTR timings still use the old partial overlay.'
			)
	if a.prepare_only:
		print('Feed and graph checked and staged; active files unchanged.')
		return
	backup = G / 'otp-pre-rail-wiki'
	backup.mkdir(exist_ok=True)
	for n in ['graph.obj', 'hk.gtfs.zip', 'build-config.json']:
		if not (backup / n).exists():
			shutil.copy2(live / n, backup / n)
	for src, dst in [
		(candidate, G / 'hk-transit-EXPERIMENTAL.gtfs.zip'),
		(stage / 'hk.gtfs.zip', live / 'hk.gtfs.zip'),
		(stage / 'graph.obj', live / 'graph.obj'),
	]:
		tmp = dst.with_name(dst.name + '.new')
		shutil.copy2(src, tmp)
		tmp.replace(dst)
	(G / 'active-rail-wiki-feed.json').write_text(json.dumps(m, indent=2))
	run([sys.executable, ROOT / 'scripts/hkrail/report.py'], OUT / 'report.log')
	print(
		'Rail wiki feed activated. Restart Python checker and OTP to load the new graph.',
		flush=True,
	)


if __name__ == '__main__':
	main()
