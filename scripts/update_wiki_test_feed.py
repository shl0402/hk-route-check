#!/usr/bin/env python3
"""Build, validate and activate the wiki-enriched feed + OTP graph. Restart server afterwards."""

import argparse, hashlib, json, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
G = ROOT / 'data/generated'
W = ROOT / 'data/wiki_pilot'


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
	ap.add_argument(
		'--activate-staged',
		action='store_true',
		help='Activate an already built and validated candidate; verify staged graph/feed consistency first',
	)
	ap.add_argument(
		'--prepare-only',
		action='store_true',
		help='Build/validate candidate only; do not alter active feed',
	)
	a = ap.parse_args()
	baseline = G / 'hk-transit-PRE-WIKI.gtfs.zip'
	candidate = G / 'hk-transit-WIKI.gtfs.zip'
	active = G / 'hk-transit-EXPERIMENTAL.gtfs.zip'
	stage = G / 'otp-wiki-stage'
	live = G / 'otp-smoke'
	if not baseline.exists():
		shutil.copy2(active, baseline)
	if not a.activate_staged:
		run([sys.executable, ROOT / 'scripts/hkbus_pilot/merge_gtfs.py'])
		run(
			[
				'java',
				'-Xmx3G',
				'-jar',
				ROOT / 'tools/gtfs-validator-8.0.1-cli.jar',
				'-i',
				candidate,
				'-o',
				G / 'wiki-validator',
				'-c',
				'HK',
				'-t',
				'2',
				'-svu',
			],
			G / 'wiki-validator.log',
		)
	validation = json.loads((G / 'wiki-validator/report.json').read_text())
	system = json.loads((G / 'wiki-validator/system_errors.json').read_text())
	if any(n['severity'] == 'ERROR' for n in validation['notices']) or system.get(
		'notices'
	):
		raise SystemExit('Validation failed; active files untouched.')
	run([sys.executable, ROOT / 'scripts/hkbus_pilot/check_merge.py'])
	stage.mkdir(exist_ok=True)
	if not a.activate_staged:
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
			G / 'wiki-otp-build.log',
		)
	if (
		sha(candidate) != sha(stage / 'hk.gtfs.zip')
		or not (stage / 'graph.obj').exists()
		or (stage / 'graph.obj').stat().st_mtime
		< (stage / 'hk.gtfs.zip').stat().st_mtime
	):
		raise SystemExit('Staged graph/feed mismatch; rebuild before activation.')
	if 'Done building graph. Exiting.' not in (G / 'wiki-otp-build.log').read_text():
		raise SystemExit('No successful graph-build log.')
	if a.prepare_only:
		print('Validated feed and graph staged; active files unchanged.', flush=True)
		return
	backup = G / 'otp-pre-wiki'
	backup.mkdir(exist_ok=True)
	for name in ['graph.obj', 'hk.gtfs.zip', 'build-config.json']:
		if not (backup / name).exists():
			shutil.copy2(live / name, backup / name)
	# Promotion happens only after all checks pass. Existing routers retain their in-memory graph until restart.
	for src, dst in [
		(candidate, active),
		(stage / 'hk.gtfs.zip', live / 'hk.gtfs.zip'),
		(stage / 'graph.obj', live / 'graph.obj'),
	]:
		tmp = dst.with_name(dst.name + '.new')
		shutil.copy2(src, tmp)
		tmp.replace(dst)
	manifest = dict(
		feed_sha256=sha(active),
		graph_sha256=sha(live / 'graph.obj'),
		provenance='wiki_provenance.json inside GTFS',
		validation_errors=0,
		report='data/wiki_pilot/merge_report.json',
	)
	(G / 'active-wiki-feed.json').write_text(json.dumps(manifest, indent=2))
	r = json.loads((W / 'merge_report.json').read_text())
	unknown = len({c['route_id'] for c in r['changes'] if not c['source_month']})
	report = f'''# Wiki GTFS update results

| Result | Count |
|---|---:|
| Scraped route titles processed | {r['normalized_articles']:,} |
| Parsed direction/day groups | {r['parsed_groups']:,} |
| Groups without parser errors | {r['clean_groups']:,} |
| Groups with unresolved parsing/semantics | {r['parsed_groups']-r['clean_groups']:,} |
| Malformed span attributes repaired | {r['repaired_span_attributes']} |
| Overlapping tables excluded | {r['unrepaired_tables']} |
| GTFS routes updated | **{r['updated_routes']}** |
| Scraped titles producing no GTFS update | {r['normalized_articles']-r['updated_titles']:,} |
| Published-departure trip records | {r['new_trips']-r['new_frequency_rows']:,} |
| Frequency trip/period records | {r['new_frequency_rows']:,} |
| Original trips calendar-adjusted | {r['original_trips_calendar_adjusted']:,} |
| Updated routes with unknown wiki update month | {unknown} |
| Independent GTFS validation errors | **0** |

Active test dates: **{r['date_range'][0]}–{r['date_range'][1]}**. OTP graph rebuilt.

Wiki source/revision is stored per trip and shown in route details. Government stops, fares and running-time estimates are retained. Unresolved/older timetables stay on government data; rail assumptions are unchanged. Real-world accuracy is not independently verified.

[Detailed changes](merge_report.json) · [Normalized data](normalized_timetables.json)
'''
	(W / 'FINDINGS.md').write_text(report)
	print(
		'Activated wiki feed and OTP graph. Restart route_checker/server.py to load them.',
		flush=True,
	)


if __name__ == '__main__':
	main()
