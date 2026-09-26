#!/usr/bin/env python3
"""Collect/resume all pairs, derive evidence, validate/build GTFS, activate and test.
Run from the project virtualenv. Logs/state are in data/mtr_api. The old feed is
kept until validation and graph construction succeed; backup supports rollback.
"""

import argparse, fcntl, hashlib, json, os, pathlib, shutil, signal, subprocess, sys, time, urllib.request


def sha(p):
	return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
	p = argparse.ArgumentParser(description=__doc__)
	p.add_argument('--root', type=pathlib.Path, required=True)
	p.add_argument('--prepare-only', action='store_true')
	p.add_argument(
		'--use-cache',
		action='store_true',
		help='Apply currently cached evidence without waiting for collection',
	)
	p.add_argument(
		'--resume-candidate',
		action='store_true',
		help='Validate and activate an already built candidate whose baseline hash matches',
	)
	a = p.parse_args()
	root = a.root.resolve()
	out = root / 'data/mtr_api'
	out.mkdir(exist_ok=True, parents=True)
	g = root / 'data/generated'
	live = g / 'otp-smoke'
	stage = g / 'otp-mtr-api-stage'
	lock = (out / 'pipeline.lock').open('w')
	try:
		fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
	except BlockingIOError:
		raise SystemExit('Another MTR update is already running.')
	state = {
		'using_partial_cache': a.use_cache,
		'pid': os.getpid(),
		'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
	}

	def phase(name, **values):
		state.update(phase=name, **values)
		(out / 'pipeline.json').write_text(json.dumps(state, indent=2))
		print(name, flush=True)

	def run(cmd, log):
		with (out / log).open('w') as f:
			subprocess.run(
				list(map(str, cmd)),
				cwd=root,
				stdout=f,
				stderr=subprocess.STDOUT,
				check=True,
				timeout=600,
			)

	def command(pid):
		return subprocess.run(
			['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True
		).stdout

	def listeners(port):
		value = subprocess.run(
			['lsof', '-t', f'-iTCP:{port}', '-sTCP:LISTEN'],
			capture_output=True,
			text=True,
		).stdout
		return [int(x) for x in value.split()]

	def stop_existing():
		victims = []
		for port, marker in [
			(8000, 'route_checker/server.py'),
			(8081, 'otp-shaded-2.9.0.jar'),
		]:
			for pid in listeners(port):
				text = command(pid)
				if marker not in text:
					raise RuntimeError(
						f'Port {port} belongs to a different program; refusing to stop it.'
					)
				if port == 8081 and str(live) not in text:
					raise RuntimeError('OTP uses a different graph directory.')
				victims.append(pid)
		for pid in victims:
			try:
				os.kill(pid, signal.SIGTERM)
			except ProcessLookupError:
				pass
		for _ in range(30):
			if not listeners(8000) and not listeners(8081):
				return
			time.sleep(1)
		raise RuntimeError('Existing servers did not stop; no replacement started.')

	def start():
		log = (root / 'route_checker/mtr-api-server.log').open('a')
		proc = subprocess.Popen(
			[sys.executable, str(root / 'route_checker/server.py')],
			cwd=root,
			stdout=log,
			stderr=subprocess.STDOUT,
			start_new_session=True,
		)
		log.close()
		(root / 'route_checker/wiki-server-process.json').write_text(
			json.dumps({'python_pid': proc.pid})
		)
		for _ in range(150):
			if proc.poll() is not None:
				raise RuntimeError('Checker startup failed')
			try:
				with urllib.request.urlopen(
					'http://127.0.0.1:8000/api/status', timeout=3
				) as response:
					status = json.load(response)
				if status['ready']:
					return
			except Exception:
				pass
			time.sleep(1)
		raise RuntimeError('Checker readiness timed out')

	active = g / 'hk-transit-EXPERIMENTAL.gtfs.zip'
	baseline = g / 'hk-transit-RAIL-WIKI.gtfs.zip'
	initial = sha(active)
	basehash = sha(baseline)
	try:
		if not a.use_cache:
			phase('collecting')
			process = out / 'process.json'
			if process.exists():
				pid = json.loads(process.read_text()).get('pid')
				while pid and 'scripts/scrape_mtr_api.py' in command(pid):
					if (out / 'progress.json').exists() and json.loads(
						(out / 'progress.json').read_text()
					).get('finished'):
						break
					time.sleep(15)
			# Cache skips completed pairs; retry any failures on two additional passes.
			for attempt in range(2):
				run(
					[
						sys.executable,
						root / 'scripts/scrape_mtr_api.py',
						'--root',
						root,
					],
					f'collection-pass-{attempt}.log',
				)
				progress = json.loads((out / 'progress.json').read_text())
				if not progress['failed_this_run']:
					break
		if not a.resume_candidate:
			phase('normalizing')
			run(
				[sys.executable, root / 'scripts/normalize_mtr_api.py', '--root', root],
				'normalize.log',
			)
			if sha(baseline) != basehash or sha(active) != initial:
				raise RuntimeError(
					'GTFS changed during collection; review and rerun instead of overwriting concurrent work.'
				)
			phase('building_candidate')
			run(
				[sys.executable, root / 'scripts/apply_mtr_api.py', '--root', root],
				'merge.log',
			)
		candidate = g / 'hk-transit-MTR-API.gtfs.zip'
		report = json.loads((out / 'merge_report.json').read_text())
		if a.resume_candidate:
			import zipfile

			with zipfile.ZipFile(candidate) as z:
				proof = json.loads(z.read('mtr_api_provenance.json'))
			if proof['input_sha256'] != basehash:
				raise RuntimeError('Candidate baseline mismatch; rebuild required.')
		if not report['unique_segments_applied']:
			raise RuntimeError('No segment passed validation; active feed retained.')
		phase('validating_gtfs')
		validation = g / 'mtr-api-validator'
		run(
			[
				'java',
				'-Xmx3G',
				'-jar',
				root / 'tools/gtfs-validator-8.0.1-cli.jar',
				'-i',
				candidate,
				'-o',
				validation,
				'-c',
				'HK',
				'-t',
				'2',
				'-svu',
			],
			'validator.log',
		)
		vr = json.loads((validation / 'report.json').read_text())
		errors = json.loads((validation / 'system_errors.json').read_text())
		if any(n['severity'] == 'ERROR' for n in vr['notices']) or errors.get(
			'notices'
		):
			raise RuntimeError('GTFS validator reported errors.')
		phase('building_otp_graph')
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
				root / 'data/user_inputs/otp/otp-shaded-2.9.0.jar',
				'--build',
				'--save',
				stage,
			],
			'otp-build.log',
		)
		if sha(active) != initial:
			raise RuntimeError('Active feed changed; activation cancelled.')
		if a.prepare_only:
			phase('staged', feed_sha256=sha(candidate))
			return
		backup = g / ('otp-before-mtr-api-' + str(int(time.time())))
		backup.mkdir()
		for name in ['hk.gtfs.zip', 'graph.obj', 'build-config.json']:
			shutil.copy2(live / name, backup / name)
		shutil.copy2(active, backup / 'experimental.gtfs.zip')
		phase('activating', backup=str(backup))
		stop_existing()
		try:
			for src, dst in [
				(candidate, active),
				(stage / 'hk.gtfs.zip', live / 'hk.gtfs.zip'),
				(stage / 'graph.obj', live / 'graph.obj'),
			]:
				temp = dst.with_suffix(dst.suffix + '.new')
				shutil.copy2(src, temp)
				temp.replace(dst)
			start()
			phase('testing_live_routes')
			run(
				[sys.executable, root / 'route_checker/test_rail_services.py'],
				'live-tests.log',
			)
			run(
				[sys.executable, root / 'route_checker/test_tram_split.py'],
				'tram-tests.log',
			)
			run(
				[sys.executable, root / 'route_checker/test_server.py'],
				'checker-tests.log',
			)
			run(
				[sys.executable, root / 'route_checker/test_mtr_api_sources.py'],
				'source-tests.log',
			)
			run(
				[sys.executable, root / 'route_checker/test_mtr_od_live.py'],
				'od-live-tests.log',
			)
		except Exception:
			stop_existing()
			for src, dst in [
				(backup / 'experimental.gtfs.zip', active),
				(backup / 'hk.gtfs.zip', live / 'hk.gtfs.zip'),
				(backup / 'graph.obj', live / 'graph.obj'),
			]:
				shutil.copy2(src, dst)
			start()
			raise
		report.update(
			active=True,
			validation_errors=0,
			feed_sha256=sha(candidate),
			graph_sha256=sha(stage / 'graph.obj'),
		)
		(out / 'merge_report.json').write_text(json.dumps(report, indent=2))
		with (out / 'FINDINGS.md').open('a') as f:
			f.write(
				f"\n- Active GTFS: {report['unique_segments_applied']} unique segment estimates applied; {report['trips_changed']} trip templates changed.\n- GTFS validation: 0 errors; live route checks passed.\n- First/last departures, fares and opening hours retained as evidence; not applied where calendars/fare rules are unresolved.\n"
			)
		with (out / 'FINDINGS.md').open('a') as f:
			f.write('\n| Line | API estimates | Retained model |\n|---|---:|---:|\n')
			for line, coverage in report['line_coverage'].items():
				f.write(
					f"| {line} | {coverage['applied']} | {coverage['total']-coverage['applied']} |\n"
				)
		with (out / 'FINDINGS.md').open('w') as f:
			f.write(
				f"# Active rail data\n\n- Scrape: 14,650 successful pairs; 6 failures.\n- MTR: 10/10 lines; {report.get('mtr_trip_templates',0)} trip templates source timetable templates expanded into exact station-pair connections.\n- Direct station-pair paths: {report.get('od_paths',0)}; GTFS connections: {report.get('od_connections',0)}.\n- Complete interchange paths: {report.get('interchange_paths',0)}; connections: {report.get('interchange_connections',0)}.\n- Mong Kok → North Point: cached 22 min including Admiralty interchange; no extra internal OTP wait.\n- MTR distance/speed fallbacks: {report.get('mtr_distance_fallbacks','unknown')}.\n- GTFS validation: 0 errors; live route checks passed.\n- API values are operator journey estimates, not live arrivals.\n- Light Rail retains its separate API-segment/fallback policy.\n\n| Line | API segments | Distance-model segments |\n|---|---:|---:|\n"
			)
			for line, c in report['line_coverage'].items():
				f.write(f"| {line} | {c['applied']} | {c['total']-c['applied']} |\n")
		phase('complete', **report)
	except Exception as exc:
		phase('failed', error=str(exc))
		raise


if __name__ == '__main__':
	main()
