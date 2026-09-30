#!/usr/bin/env python3
"""One-command local research setup. Python 3.11+, Java 25, macOS/Linux/WSL."""

from __future__ import annotations
import argparse, csv, hashlib, importlib.metadata, io, json, os, re, shutil, socket
import subprocess, sys, tarfile, time, zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent
RAW = 'data/raw/2026-09-17'  # Stable storage layout, NOT the current snapshot date.
META = 'data/mtr_api_audit/2026-09-21'
OTP = 'data/user_inputs/otp/otp-shaded-2.9.0.jar'
VALIDATOR = 'tools/gtfs-validator-8.0.1-cli.jar'
SOURCES = {
	'td': (
		f'{RAW}/td/gtfs.zip',
		'https://static.data.gov.hk/td/pt-headway-en/gtfs.zip',
	),
	'osm': (
		f'{RAW}/osm/hong-kong-latest.osm.pbf',
		'https://download.geofabrik.de/asia/china/hong-kong-latest.osm.pbf',
	),
	'mtr_stations': (
		'data/user_inputs/mtr/mtr_lines_and_stations.csv',
		'https://opendata.mtr.com.hk/data/mtr_lines_and_stations.csv',
	),
	'lr_stations': (
		'data/user_inputs/mtr/light_rail_routes_and_stops.csv',
		'https://opendata.mtr.com.hk/data/light_rail_routes_and_stops.csv',
	),
	'headways': (
		f'{RAW}/mtr/service_hours.html',
		'https://www.mtr.com.hk/en/customer/services/train_service_index.html',
	),
	'bus_identity': (
		f'{RAW}/td/routes_fares/JSON_BUS.json',
		'https://static.data.gov.hk/td/routes-fares-geojson/JSON_BUS.json',
	),
	'minibus_identity': (
		f'{RAW}/td/routes_fares/JSON_GMB.json',
		'https://static.data.gov.hk/td/routes-fares-geojson/JSON_GMB.json',
	),
	'planner': (
		f'{META}/journey_planner.html',
		'https://www.mtr.com.hk/en/customer/jp/index.php',
	),
}
TOOLS = {
	OTP: (
		'https://repo.maven.apache.org/maven2/org/opentripplanner/otp-shaded/2.9.0/otp-shaded-2.9.0.jar',
		'6bcd699f9a5d3f4f1ef213aa9a629ac2e808fb771fef3ba137df6c8e56f6b9ff',
	),
	VALIDATOR: (
		'https://github.com/MobilityData/gtfs-validator/releases/download/v8.0.1/gtfs-validator-8.0.1-cli.jar',
		'19293ddd9b6f954f216d4f12054bd8a3232921751c4484339e339764a91000e2',
	),
}
CACHE_EXACT = {v[0] for v in SOURCES.values()} | {
	f'{META}/heavyRailDetails.json',
	f'{META}/lightRailDetails.json',
	'data/source_manifest.json',
	'data/landsd/raw/igeocom.zip',
	'data/landsd/raw/indoor-network.zip',
	'data/landsd/raw/indoor-network-fgdb.zip',
	'data/surface/raw/bus.zip',
	'data/surface/raw/gmb.zip',
}
CACHE_PREFIX = (
	'data/wiki_pilot/raw/',
	'data/rail_wiki/raw/',
	'data/mtr_api/raw/',
	'data/derived/mtr_service_hours/',
	'data/landsd/raw/',
	'data/surface/raw/',
	'data/operators/raw/',
)


def now():
	return datetime.now(timezone.utc).isoformat()


def digest(path):
	h = hashlib.sha256()
	with Path(path).open('rb') as f:
		for block in iter(lambda: f.read(1024 * 1024), b''):
			h.update(block)
	return h.hexdigest()


def save(path, value):
	path = Path(path)
	path.parent.mkdir(parents=True, exist_ok=True)
	temp = path.with_name(path.name + '.part')
	temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
	temp.replace(path)


def read(path, default=None):
	return (
		json.loads(Path(path).read_text(encoding="utf-8-sig"))
		if Path(path).exists()
		else default
	)


def allowed_cache(name):
	p = PurePosixPath(name)
	return (
		not p.is_absolute()
		and '..' not in p.parts
		and '\\' not in name
		and (
			name in CACHE_EXACT
			or name.startswith(CACHE_PREFIX)
			and (p.suffix in ('.json', '.html') or name.startswith('data/landsd/raw/') and p.suffix == '.geojson'
				or name.startswith('data/operators/raw/') and p.suffix in ('.csv', '.pdf'))
		)
	)


def cache_files(root=ROOT):
	files = {root / n for n in CACHE_EXACT if (root / n).is_file()}
	for prefix in CACHE_PREFIX:
		files.update(
			p
			for p in (root / prefix).rglob('*')
			if p.is_file() and allowed_cache(p.relative_to(root).as_posix())
		)
	return sorted(files)


def export_cache(path):
	files = cache_files(ROOT)
	if not files:
		raise ValueError('No source cache yet. Run python3 run.py fetch first.')
	manifest = {
		'format': 1,
		'created_at': now(),
		'license': 'Third-party sources; see DATA_SOURCES.md. Not covered by the code MIT license.',
		'files': {
			p.relative_to(ROOT).as_posix(): {
				'sha256': digest(p),
				'bytes': p.stat().st_size,
			}
			for p in files
		},
	}
	path = Path(path).resolve()
	path.parent.mkdir(parents=True, exist_ok=True)
	temp = path.with_name(path.name + '.part')
	with tarfile.open(temp, 'w:gz', compresslevel=5) as tar:
		body = json.dumps(manifest).encode()
		info = tarfile.TarInfo('cache-manifest.json')
		info.size = len(body)
		tar.addfile(info, io.BytesIO(body))
		for p in files:
			tar.add(p, arcname=p.relative_to(ROOT).as_posix(), recursive=False)
	temp.replace(path)
	print(
		f'Cache: {path} · {len(files):,} files · {path.stat().st_size/1e6:.1f} MB\nSHA256 {digest(path)}',
		flush=True,
	)


def import_cache(path):
	# Validate the ENTIRE archive before replacing any existing sources. Never extract links or executable code.
	with tarfile.open(path, 'r:gz') as tar:
		members = tar.getmembers()
		names = [m.name for m in members]
		if len(names) != len(set(names)):
			raise ValueError('Duplicate cache paths')
		if any(not m.isfile() for m in members):
			raise ValueError('Cache must contain regular files only')
		if sum(m.size for m in members) > 5 * 1024**3:
			raise ValueError('Cache exceeds 5 GB unpacked limit')
		meta = tar.getmember('cache-manifest.json')
		if meta.size > 20 * 1024**2:
			raise ValueError('Oversized cache manifest')
		manifest = json.load(tar.extractfile(meta))
		if manifest.get('format') != 1:
			raise ValueError('Unsupported cache format')
		expected = manifest['files']
		if set(names) != (set(expected) | {'cache-manifest.json'}):
			raise ValueError('Cache file list does not match manifest')
		for m in members:
			if m.name == 'cache-manifest.json':
				continue
			if not allowed_cache(m.name):
				raise ValueError(f'Unexpected cache path: {m.name}')
			# Also reject a pre-existing local symlink that could redirect a valid archive path.
			target = ROOT / m.name
			if not target.resolve().is_relative_to(ROOT.resolve()):
				raise ValueError('Cache target leaves project')
			h = hashlib.sha256()
			with tar.extractfile(m) as stream:
				for block in iter(lambda: stream.read(1024 * 1024), b''):
					h.update(block)
			if (
				m.size != expected[m.name]['bytes']
				or h.hexdigest() != expected[m.name]['sha256']
			):
				raise ValueError(f'Cache checksum mismatch: {m.name}')
		for m in members:
			if m.name == 'cache-manifest.json':
				continue
			target = ROOT / m.name
			target.parent.mkdir(parents=True, exist_ok=True)
			temp = target.with_name(target.name + '.importing')
			with tar.extractfile(m) as source, temp.open('wb') as dest:
				shutil.copyfileobj(source, dest)
			temp.replace(target)
	print(f'Imported and verified {len(expected):,} cached source files.', flush=True)


def bootstrap(offline):
	requirements = []
	for line in (ROOT / 'requirements.txt').read_text().splitlines():
		if line.strip() and not line.startswith('#'):
			requirements.append(line.strip().split('=='))
	try:
		installed = all(
			importlib.metadata.version(name) == version
			for name, version in requirements
		)
	except importlib.metadata.PackageNotFoundError:
		installed = False
	if installed:
		return
	python = ROOT / '.venv/bin/python'
	if Path(sys.prefix).resolve() == (ROOT / '.venv').resolve():
		if offline:
			raise ValueError(
				'Python dependencies missing. Install requirements.txt once before an offline build.'
			)
		subprocess.run(
			[
				sys.executable,
				'-m',
				'pip',
				'install',
				'-r',
				str(ROOT / 'requirements.txt'),
			],
			check=True,
		)
		return
	if not python.exists():
		if offline:
			raise ValueError(
				'No prepared Python environment. Run once online to install requirements.txt.'
			)
		subprocess.run([sys.executable, '-m', 'venv', str(ROOT / '.venv')], check=True)
	os.execv(str(python), [str(python), str(ROOT / 'run.py'), *sys.argv[1:]])


def check_runtime():
	for name in ('java', 'curl'):
		if not shutil.which(name):
			raise ValueError(f'Missing {name}; see README.md prerequisites.')
	result = subprocess.run(['java', '-version'], capture_output=True, text=True)
	match = re.search(r'version "(\d+)', result.stdout + result.stderr)
	if result.returncode or not match or int(match[1]) < 25:
		raise ValueError(
			'OTP 2.9 requires Java 25+. Install Java 25 and check java -version.'
		)


def download(relative, url, offline=False, refresh=False, checksum=None):
	path = ROOT / relative
	if path.exists() and not refresh:
		if checksum and digest(path) != checksum:
			raise ValueError(
				f'Checksum failed: {relative}. Remove that file and retry.'
			)
		return False
	if offline:
		raise ValueError(
			f'Offline source missing: {relative}. Rerun without --offline.'
		)
	path.parent.mkdir(parents=True, exist_ok=True)
	temp = path.with_name(path.name + '.download')
	print('Download:', url, flush=True)
	subprocess.run(
		[
			'curl',
			'--fail',
			'--location',
			'--retry',
			'3',
			'--connect-timeout',
			'30',
			'--max-time',
			'1200',
			'--output',
			str(temp),
			url,
		],
		check=True,
	)
	if not temp.stat().st_size:
		raise ValueError(f'Empty response from {url}')
	if checksum and digest(temp) != checksum:
		raise ValueError(f'Download checksum failed: {url}')
	temp.replace(path)
	return True


def command(args, root=ROOT, log=None):
	print('  >', ' '.join(str(a) for a in args), flush=True)
	if log:
		log.parent.mkdir(parents=True, exist_ok=True)
		print(f'    Log: {log}', flush=True)
		with log.open('w') as stream:
			subprocess.run(
				list(map(str, args)),
				cwd=root,
				stdout=stream,
				stderr=subprocess.STDOUT,
				check=True,
			)
	else:
		subprocess.run(list(map(str, args)), cwd=root, check=True)


def script(name, *args, root=ROOT, log=None):
	command([sys.executable, root / name, *args], root, log)


def fetch(a):
	manifest = read(ROOT / 'data/source_manifest.json', {})
	for key, (path, url) in SOURCES.items():
		changed = download(path, url, a.offline, a.refresh)
		if changed or key not in manifest:
			manifest[key] = {
				'path': path,
				'url': url,
				'sha256': digest(ROOT / path),
				'retrieved_at': now() if changed else None,
				'snapshot': None if changed else 'unknown (pre-existing cache)',
			}
		elif digest(ROOT / path) != manifest[key]['sha256']:
			raise ValueError(
				f'Cached source changed: {path}; use --refresh to download a coherent snapshot.'
			)
	save(ROOT / 'data/source_manifest.json', manifest)
	# These are inline JSON objects in the official journey-planner HTML, not invented station coordinates.
	html = (ROOT / SOURCES['planner'][0]).read_text()
	for variable in ('heavyRailDetails', 'lightRailDetails'):
		match = re.search(r'\bvar\s+' + variable + r'\s*=\s*', html)
		if not match:
			raise ValueError(f'MTR page format changed: missing {variable}')
		obj, _ = json.JSONDecoder().raw_decode(html[match.end() :])
		if not obj.get('stations') or not obj.get('lines'):
			raise ValueError('MTR metadata missing stations/lines')
		save(ROOT / META / (variable + '.json'), obj)
	with zipfile.ZipFile(ROOT / SOURCES['td'][0]) as z:
		for name in (
			'routes.txt',
			'trips.txt',
			'stops.txt',
			'stop_times.txt',
			'calendar.txt',
			'calendar_dates.txt',
		):
			z.getinfo(name)
	for key in ('bus_identity', 'minibus_identity'):
		if not read(ROOT / SOURCES[key][0]).get('features'):
			raise ValueError(f'{key}: no route features')
	if a.offline:
		with (ROOT / SOURCES['mtr_stations'][0]).open(encoding='utf-8-sig') as f:
			origins = {
				r['Station ID']
				for r in csv.DictReader(f)
				if r['Sequence'] and float(r['Sequence']) == 1
			}
		for sid in origins:
			if not (ROOT / f'data/derived/mtr_service_hours/{sid}.html').exists():
				raise ValueError(f'Missing cached first/last timetable: {sid}')
	else:
		script('scripts/refresh_mtr_sources.py', *(['--force'] if a.refresh else []))
	flags = (['--offline'] if a.offline else []) + (['--refresh'] if a.refresh else [])
	print(
		'\n[Sources 1/4] Bus/minibus directory and selected route articles', flush=True
	)
	script(
		'scripts/hkbus_pilot/scraper.py',
		'--max-directories',
		'128',
		'--page',
		'九巴98D線',
		*flags,
	)
	inventory = read(ROOT / 'data/wiki_pilot/inventory.json')
	if inventory.get('pending_directories'):
		raise ValueError('Wiki directory limit reached; inventory incomplete.')
	script('scripts/hkbus_pilot/bulk.py', '--retry-failed', *flags)
	print('\n[Sources 2/4] Heavy rail and Light Rail wiki', flush=True)
	script('scripts/hkrail/scraper.py', *flags)
	print(
		'\n[Sources 3/4] All ordered station pairs within HR and LR (resumable)',
		flush=True,
	)
	if a.refresh:
		# A fresh run must not silently retain the previous successful API responses.
		old = ROOT / 'data/mtr_api/raw'
		if old.exists():
			old.rename(old.with_name('raw-before-refresh-' + str(time.time_ns())))
	script(
		'scripts/scrape_mtr_api.py',
		'--root',
		ROOT,
		*(['--offline'] if a.offline else []),
	)
	# Fail closed for missing/error pairs except the six explicit co-located AEL/TCL aliases.
	script('scripts/scrape_mtr_api.py', '--root', ROOT, '--offline')
	print('\n[Sources 4/4] LandsD places and station interiors (resumable)', flush=True)
	script('scripts/landsd_enrich.py', 'fetch', '--root', ROOT, *flags)
	script('scripts/indoor_network.py', 'fetch', '--root', ROOT, *flags)
	script('scripts/surface_timing.py', 'fetch', '--root', ROOT, *flags)
	script('scripts/operator_sources.py', 'fetch', '--root', ROOT, *flags)
	for path, (url, sha) in TOOLS.items():
		download(path, url, a.offline, checksum=sha)
	print(
		'Source collection complete. Raw responses and revisions retained.', flush=True
	)


def copy_link(source, target):
	target.parent.mkdir(parents=True, exist_ok=True)
	if target.exists():
		target.unlink()
	try:
		os.link(source, target)
	except OSError:
		shutil.copy2(source, target)


def fingerprint(a):
	h = hashlib.sha256()
	for p in sorted(
		[
			ROOT / 'run.py',
			ROOT / 'requirements.txt',
			*(ROOT / 'scripts').rglob('*.py'),
			*cache_files(ROOT),
		]
	):
		h.update(str(p.relative_to(ROOT)).encode())
		h.update(digest(p).encode())
	h.update(f'{a.start_date}/{a.days}'.encode())
	return h.hexdigest()


def build(a):
	fetch(a)
	start = date.fromisoformat(a.start_date)
	end = start + timedelta(days=a.days - 1)
	key = fingerprint(a)
	work = ROOT / 'data/build-work'
	statepath = ROOT / 'data/setup-progress.json'
	state = read(statepath, {})
	if state.get('fingerprint') != key:
		state = {'fingerprint': key, 'started_at': now(), 'completed': []}
	work.mkdir(parents=True, exist_ok=True)
	shutil.copytree(
		ROOT / 'scripts',
		work / 'scripts',
		dirs_exist_ok=True,
		ignore=shutil.ignore_patterns('__pycache__'),
	)
	for p in cache_files(ROOT):
		copy_link(p, work / p.relative_to(ROOT))
	for p in TOOLS:
		copy_link(ROOT / p, work / p)
	copy_link(
		ROOT / 'data/mtr_api/inventory.json', work / 'data/mtr_api/inventory.json'
	)
	gen = work / 'data/generated'
	gen.mkdir(parents=True, exist_ok=True)
	logs = ROOT / 'data/setup-logs'

	def base_build():
		script(
			'scripts/build_gtfs.py',
			'--allow-estimates',
			'--start-date',
			a.start_date,
			'--days',
			str(a.days),
			root=work,
		)
		shutil.copy2(
			gen / 'hk-transit-EXPERIMENTAL.gtfs.zip',
			gen / 'hk-transit-PRE-WIKI.gtfs.zip',
		)

	steps = [
		(
			'01-osm',
			lambda: script('scripts/extract_osm_rail.py', root=work),
			work / 'data/derived/osm_rail_features.json',
		),
		(
			'02-api-normalize',
			lambda: script('scripts/normalize_mtr_api.py', '--root', work, root=work),
			work / 'data/mtr_api/normalized.json',
		),
		('03-base-feed', base_build, gen / 'hk-transit-PRE-WIKI.gtfs.zip'),
		(
			'04-bus-audit',
			lambda: script('scripts/hkbus_pilot/audit.py', root=ROOT),
			ROOT / 'data/wiki_pilot/audit_summary.json',
		),
	]
	# Reuse normalized article extraction in the isolated build; never copy an old generated GTFS.
	for name in ('wiki_pilot', 'rail_wiki'):
		folder = ROOT / 'data' / name
		for child in ('articles', 'supplements', 'directories'):
			for p in (folder / child).rglob('*.json'):
				copy_link(p, work / p.relative_to(ROOT))
		for child in (
			'inventory.json',
			'inventory_comparison.json',
			'bulk_comparison.json',
		):
			p = folder / child
			if p.exists():
				copy_link(p, work / p.relative_to(ROOT))
		# Older builds linked reports as inputs. Detach those before any writer runs.
		for child in (
			'merge_report.json',
			'normalized.json',
			'normalization_report.json',
			'normalized_timetables.json',
		):
			original = folder / child
			candidate = work / 'data' / name / child
			if (
				original.exists()
				and candidate.exists()
				and candidate.samefile(original)
			):
				temp = candidate.with_name(candidate.name + '.detached')
				shutil.copy2(candidate, temp)
				temp.replace(candidate)

	def bus_merge():
		for name in ('audit_routes.json', 'audit_summary.json', 'bulk_comparison.json'):
			copy_link(ROOT / 'data/wiki_pilot' / name, work / 'data/wiki_pilot' / name)
		script(
			'scripts/hkbus_pilot/merge_gtfs.py',
			'--start',
			a.start_date,
			'--end',
			str(end),
			root=work,
		)

	def rail_merge():
		shutil.copy2(
			gen / 'hk-transit-WIKI.gtfs.zip', gen / 'hk-transit-PRE-RAIL-WIKI.gtfs.zip'
		)
		script('scripts/hkrail/normalize.py', root=work)
		script('scripts/hkrail/merge_gtfs.py', root=work)

	def validate():
		shutil.copy2(
			gen / 'hk-transit-OPERATOR.gtfs.zip',
			gen / 'hk-transit-EXPERIMENTAL.gtfs.zip',
		)
		script('scripts/validate_gtfs.py', root=work)
		script('scripts/check_surface_feed.py', gen / 'hk-transit-INDOOR.gtfs.zip', gen / 'hk-transit-SURFACE.gtfs.zip', root=work)
		script('scripts/hkbus_pilot/check_merge.py', root=work)
		script('scripts/hkrail/check_merge.py', root=work)

	graph = gen / 'otp-smoke'

	def graph_build():
		graph.mkdir(exist_ok=True)
		shutil.copy2(gen / 'hk-transit-EXPERIMENTAL.gtfs.zip', graph / 'hk.gtfs.zip')
		copy_link(work / SOURCES['osm'][0], graph / 'hong-kong.osm.pbf')
		save(
			graph / 'build-config.json',
			{
				'transitServiceStart': a.start_date,
				'transitServiceEnd': str(end + timedelta(days=1)),
			},
		)
		script('scripts/indoor_timing.py', graph, root=work)
		command(
			['java', '-Xmx4G', '-jar', work / OTP, '--build', '--save', graph],
			work,
			logs / 'otp-build.log',
		)

	steps += [
		('05-bus-merge', bus_merge, gen / 'hk-transit-WIKI.gtfs.zip'),
		('06-rail-wiki', rail_merge, gen / 'hk-transit-RAIL-WIKI.gtfs.zip'),
		(
			'07-mtr-whole-journeys',
			lambda: script('scripts/apply_mtr_api.py', '--root', work, root=work),
			gen / 'hk-transit-MTR-API.gtfs.zip',
		),
		('08-landsd', lambda: script('scripts/landsd_enrich.py', 'merge', '--root', work, root=work), gen / 'hk-transit-LANDSD.gtfs.zip'),
		('08b-indoor', lambda: script('scripts/indoor_network.py', 'compile', '--root', work, root=work), gen / 'hk-transit-INDOOR.gtfs.zip'),
		('08c-surface-timing', lambda: script('scripts/surface_timing.py', 'merge', '--root', work, root=work), gen / 'hk-transit-SURFACE.gtfs.zip'),
		('08d-operator-timetables', lambda: script('scripts/operator_timing.py', '--root', work, root=work), gen / 'hk-transit-OPERATOR.gtfs.zip'),
		('09-validate', validate, gen / 'validator/report.json'),
		('10-otp-graph', graph_build, graph / 'graph.obj'),
	]
	# Once any saved output is missing/changed, rerun that step and all dependent steps.
	rerun = False
	for index, (name, fn, output) in enumerate(steps, 1):
		valid = output.exists() and state.get('outputs', {}).get(name) == digest(output)
		if name in state['completed'] and valid and not rerun:
			print(f'[{index}/{len(steps)}] {name}: verified, reused', flush=True)
			continue
		rerun = True
		state['completed'] = [
			x for x in state['completed'] if x in [s[0] for s in steps[: index - 1]]
		]
		state.pop('error', None)
		state.update(current=name, status='running', updated_at=now())
		save(statepath, state)
		print(f'\n[{index}/{len(steps)}] {name}', flush=True)
		try:
			fn()
		except Exception as exc:
			state.update(status='failed', error=str(exc), updated_at=now())
			save(statepath, state)
			raise
		state['completed'].append(name)
		state.setdefault('outputs', {})[name] = digest(output)
		save(statepath, state)
	# Publish only after both independent GTFS validation and graph compilation succeeded.
	live = ROOT / 'data/generated'
	live.mkdir(parents=True, exist_ok=True)
	for p in [
		gen / 'hk-transit-EXPERIMENTAL.gtfs.zip',
		gen / 'quality_report.json',
		*graph.iterdir(),
	]:
		relative = p.relative_to(gen)
		dest = live / relative
		dest.parent.mkdir(parents=True, exist_ok=True)
		temp = dest.with_name(dest.name + '.new')
		shutil.copy2(p, temp)
		temp.replace(dest)
	for name in (
		'wiki_pilot/merge_report.json',
		'rail_wiki/merge_report.json',
		'mtr_api/merge_report.json',
		'landsd/merge_report.json',
		'landsd/places.geojson',
		'landsd/station_facilities.geojson',
		'landsd/station_levels.geojson',
		'landsd/RESULTS.md',
		'surface/report.json',
		'operators/report.json',
	):
		if (work / 'data' / name).exists():
			dest = ROOT / 'data' / name
			dest.parent.mkdir(parents=True, exist_ok=True)
			temp = dest.with_name(dest.name + '.new')
			shutil.copy2(work / 'data' / name, temp)
			temp.replace(dest)
	# The autocomplete index depends on the final feed and OSM snapshot.
	shutil.copytree(work / 'data/landsd/indoor', ROOT / 'data/landsd/indoor', dirs_exist_ok=True)
	(ROOT / 'route_checker/places.json').unlink(missing_ok=True)
	manifest = {
		'built_at': now(),
		'fingerprint': key,
		'date_range': [a.start_date, str(end)],
		'gtfs_sha256': digest(live / 'hk-transit-EXPERIMENTAL.gtfs.zip'),
		'graph_sha256': digest(live / 'otp-smoke/graph.obj'),
		'router_config_sha256': digest(live / 'otp-smoke/router-config.json'),
		'build_config_sha256': digest(live / 'otp-smoke/build-config.json'),
		'validation_errors': 0,
		'indoor_validation_sha256': digest(ROOT / 'data/landsd/indoor/validation.json'),
		'surface_report_sha256': digest(ROOT / 'data/surface/report.json'),
		'operator_report_sha256': digest(ROOT / 'data/operators/report.json'),
		'source_manifest': read(ROOT / 'data/source_manifest.json'),
	}
	save(live / 'release-manifest.json', manifest)
	state.update(status='complete', current=None, finished_at=now())
	save(statepath, state)
	print(f'\nBuild ready: {start}–{end}. Validation: zero errors.', flush=True)


def check_build():
	m = read(ROOT / 'data/generated/release-manifest.json')
	if not m:
		raise ValueError('No verified build. Run python3 run.py build first.')
	for path, key in [
		('hk-transit-EXPERIMENTAL.gtfs.zip', 'gtfs_sha256'),
		('otp-smoke/graph.obj', 'graph_sha256'),
	]:
		if digest(ROOT / 'data/generated' / path) != m[key]:
			raise ValueError(f'Built artifact changed: {path}; rebuild before serving.')
	if m.get('surface_report_sha256') and digest(ROOT / 'data/surface/report.json') != m['surface_report_sha256']:
		raise ValueError('Surface timing report does not match the active release')
	if m.get('operator_report_sha256') and digest(ROOT / 'data/operators/report.json') != m['operator_report_sha256']:
		raise ValueError('Operator timetable report does not match the active release')
	if m.get('indoor_validation_sha256') and digest(ROOT / 'data/landsd/indoor/validation.json') != m['indoor_validation_sha256']:
		raise ValueError('Indoor validation report does not match the active release')
	for filename, key in (('router-config.json', 'router_config_sha256'), ('build-config.json', 'build_config_sha256')):
		if m.get(key) and digest(ROOT / 'data/generated/otp-smoke' / filename) != m[key]:
			raise ValueError(f'Routing timing configuration changed: {filename}; rebuild before serving.')
	print(
		'Verified GTFS + OTP graph. Service dates:',
		' to '.join(m['date_range']),
		flush=True,
	)


def ports_free(a):
	if a.port == a.otp_port:
		raise ValueError('Web and OTP ports must differ')
	for port in (a.port, a.otp_port):
		with socket.socket() as s:
			try:
				s.bind(('127.0.0.1', port))
			except OSError:
				raise ValueError(
					f'Port {port} is already in use. Stop that server or choose --port and --otp-port; setup never kills another process.'
				)


def main():
	p = argparse.ArgumentParser(description=__doc__)
	p.add_argument(
		'command',
		nargs='?',
		choices=['start', 'build', 'fetch', 'check', 'status', 'export-cache'],
		default='start',
	)
	p.add_argument(
		'--cache',
		type=Path,
		help='Import a checksum-verified raw-source .tar.gz cache before building',
	)
	p.add_argument(
		'--output',
		type=Path,
		default=ROOT / 'dist/hk-routing-source-cache.tar.gz',
		help='Cache export destination',
	)
	p.add_argument(
		'--offline',
		action='store_true',
		help='Require all source files, dependencies and JARs locally; no downloads',
	)
	p.add_argument(
		'--refresh',
		action='store_true',
		help='Fetch a new source snapshot, including all station pairs',
	)
	p.add_argument(
		'--start-date',
		default=str(date.today()),
		help='Start of the experimental service window (YYYY-MM-DD)',
	)
	p.add_argument('--days', type=int, default=30)
	p.add_argument('--port', type=int, default=8000)
	p.add_argument('--otp-port', type=int, default=8081)
	a = p.parse_args()
	if sys.version_info < (3, 11):
		p.error('Python 3.11+ required')
	if os.name == 'nt':
		p.error('On Windows, run inside WSL2 (the collectors use Unix file locks).')
	if a.offline and a.refresh:
		p.error('--offline and --refresh cannot be combined')
	if not 1 <= a.days <= 90:
		p.error('--days must be 1..90')
	if a.command == 'status':
		for name in (
			'data/setup-progress.json',
			'data/wiki_pilot/bulk_status.json',
			'data/mtr_api/progress.json',
			'data/landsd/progress.json',
		):
			print(
				name,
				json.dumps(
					read(ROOT / name, {'status': 'not started'}),
					ensure_ascii=False,
					indent=2,
				),
			)
		return
	# Single writer, including import/export. A crash releases the OS lock automatically.
	import fcntl

	(ROOT / 'data').mkdir(exist_ok=True)
	with (ROOT / 'data/setup.lock').open('a') as lock:
		try:
			fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
		except BlockingIOError:
			raise ValueError('Setup already running. Use python3 run.py status.')
		if a.command == 'export-cache':
			export_cache(a.output)
			return
		if a.command == 'check':
			check_build()
			return
		if a.command in ('start', 'build'):
			ports_free(a)
		bootstrap(a.offline)
		check_runtime()
		if a.cache:
			import_cache(a.cache)
		if a.command == 'fetch':
			fetch(a)
			return
		explicit_dates = any(
			x == '--start-date'
			or x.startswith('--start-date=')
			or x == '--days'
			or x.startswith('--days=')
			for x in sys.argv[1:]
		)
		if (
			a.command == 'build'
			or a.cache
			or a.refresh
			or explicit_dates
			or not (ROOT / 'data/generated/release-manifest.json').exists()
		):
			build(a)
		check_build()
	if a.command == 'start':
		print(
			f'Open http://127.0.0.1:{a.port} once ready. Ctrl+C stops the checker and its OTP process.',
			flush=True,
		)
		script(
			'route_checker/server.py',
			'--port',
			str(a.port),
			'--otp-port',
			str(a.otp_port),
		)


if __name__ == '__main__':
	try:
		main()
	except KeyboardInterrupt:
		print('\nStopped. Rerun the same command to resume cached work.')
		sys.exit(130)
	except (
		ValueError,
		OSError,
		subprocess.CalledProcessError,
		KeyError,
		tarfile.TarError,
	) as exc:
		print(
			f'\nSetup stopped: {exc}\nFix the reported source/runtime issue and rerun. Progress: python3 run.py status',
			file=sys.stderr,
		)
		sys.exit(1)
