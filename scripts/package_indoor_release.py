#!/usr/bin/env python3
"""Prepare a checksummed routing-only release for the self-contained w8g server.

Reads the target to capture expected-old hashes and patch its authenticated
indoor endpoint. Does not write to the target or run a server.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil


def digest(path):
	if not path.exists():
		return None
	h = hashlib.sha256()
	with path.open('rb') as stream:
		for block in iter(lambda: stream.read(1024 * 1024), b''):
			h.update(block)
	return h.hexdigest()


INSTALLER = '''"""Install a reviewed routing release after stopping the target API and OTP."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import socket

def digest(p):
\tif not p.exists(): return None
\th = hashlib.sha256()
\twith p.open('rb') as f:
\t\tfor b in iter(lambda: f.read(1048576), b''): h.update(b)
\treturn h.hexdigest()

def stopped(ports):
\tfor port in ports:
\t\tfor host in ('127.0.0.1', '::1'):
\t\t\ttry:
\t\t\t\twith socket.create_connection((host, port), timeout=.3):
\t\t\t\t\traise RuntimeError(f'Port {port} is running. Stop the server with Control-C first.')
\t\t\texcept OSError: pass

def install(bundle, target, ports, check=False):
\trows = json.loads((bundle / 'manifest.json').read_text())['files']
\tfor r in rows:
\t\tname = Path(r['path'])
\t\tif name.is_absolute() or '..' in name.parts or name.parts[0] not in ('data', 'route_checker', 'scripts', 'tools', 'docs', 'server', 'requirements-indoor.txt'):
\t\t\traise ValueError('Invalid release path')
\t\tsrc, dst = bundle / 'payload' / name, target / name
\t\tif not src.resolve().is_relative_to((bundle / 'payload').resolve()) or not dst.resolve().is_relative_to(target.resolve()):
\t\t\traise ValueError('Release path escapes root')
\t\tif digest(src) != r['sha256'] or digest(dst) not in (r['previous_sha256'], r['sha256']):
\t\t\traise ValueError('Changed file; review before installing: ' + str(name))
\tif check:
\t\tprint('Verified candidate and target:', len(rows), 'files; no files installed')
\t\treturn
\tstopped(ports)
\tbackup = target / 'data/routing_backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
\tprepared = []
\ttry:
\t\tfor r in rows:
\t\t\tdst, old = target / r['path'], backup / r['path']
\t\t\tif dst.exists():
\t\t\t\told.parent.mkdir(parents=True, exist_ok=True)
\t\t\t\tshutil.copy2(dst, old)
\t\t\tdst.parent.mkdir(parents=True, exist_ok=True)
\t\t\ttmp = dst.with_name(dst.name + '.indoor-incoming')
\t\t\tshutil.copy2(bundle / 'payload' / r['path'], tmp)
\t\t\tprepared.append((dst, old, tmp))
\t\t\tif digest(tmp) != r['sha256']: raise ValueError('Copy checksum mismatch')
\t\tstopped(ports)
\t\tfor dst, old, tmp in prepared: tmp.replace(dst)
\texcept Exception:
\t\tfor dst, old, tmp in prepared:
\t\t\tif old.exists(): shutil.copy2(old, dst)
\t\t\telif not tmp.exists(): dst.unlink(missing_ok=True)
\t\t\ttmp.unlink(missing_ok=True)
\t\traise
\tbackup.mkdir(parents=True, exist_ok=True)
\tshutil.copy2(bundle / 'manifest.json', backup / 'release.json')
\tprint('Installed', len(rows), 'files. Backup:', backup)
\tprint('Start the server yourself: ./start.command')

if __name__ == '__main__':
\tp = argparse.ArgumentParser(description=__doc__)
\tp.add_argument('--target', type=Path, required=True)
\tp.add_argument('--ports', type=int, nargs='+', default=[8787,8092])
\tp.add_argument('--check', action='store_true')
\ta = p.parse_args()
\tinstall(Path(__file__).resolve().parent, a.target.resolve(), a.ports, a.check)
'''


def package(root, target, output):
	payload = output / 'payload'
	payload.mkdir(parents=True, exist_ok=True)
	files = [
		'data/generated/hk-transit-EXPERIMENTAL.gtfs.zip',
		'data/generated/otp-smoke/hk.gtfs.zip',
		'data/generated/otp-smoke/graph.obj',
		'data/generated/otp-smoke/build-config.json',
		'data/generated/otp-smoke/router-config.json',
		'data/generated/otp-smoke/hong-kong.osm.pbf',
		'data/generated/release-manifest.json',
		'route_checker/server.py', 'route_checker/check_indoor_routes.py',
		'route_checker/check_mtr_routes.py', 'route_checker/check_surface_routes.py',
		'scripts/landsd_enrich.py', 'scripts/indoor_network.py', 'scripts/indoor_matching.py', 'scripts/indoor_routing.py', 'scripts/rebuild_indoor.py',
		'scripts/indoor_timing.py',
		'scripts/surface_timing.py', 'scripts/rebuild_surface.py', 'scripts/check_surface_feed.py',
		'docs/SURFACE_TIMING.md', 'data/surface/report.json',
		'data/mtr_api/inventory.json',
		'docs/INDOOR_ROUTING.md', 'docs/INDOOR_VALIDATION.md', 'tools/gtfs-validator-8.0.1-cli.jar',
	]
	files += [p.relative_to(root).as_posix() for p in (root / 'data/mtr_api/raw').glob('HR_*.json')]
	for prefix in ('data/landsd/raw', 'data/landsd/indoor', 'data/surface/raw'):
		files += [p.relative_to(root).as_posix() for p in (root / prefix).rglob('*') if p.is_file() and not p.name.startswith('.') and not p.name.endswith('.part')]
	for name in files:
		p = payload / name
		p.parent.mkdir(parents=True, exist_ok=True)
		shutil.copy2(root / name, p)
	surface_base = payload / 'data/surface-source/hk-transit-INDOOR.gtfs.zip'
	surface_base.parent.mkdir(parents=True, exist_ok=True)
	shutil.copy2(root / 'data/build-work/data/generated/hk-transit-INDOOR.gtfs.zip', surface_base)
	(surface_base.parent / 'manifest.json').write_text(json.dumps(dict(sha256=digest(surface_base)), indent=2))
	base = payload / 'data/indoor-source/hk-transit-LANDSD.gtfs.zip'
	base.parent.mkdir(parents=True, exist_ok=True)
	shutil.copy2(root / 'data/build-work/data/generated/hk-transit-LANDSD.gtfs.zip', base)
	(base.parent / 'manifest.json').write_text(json.dumps(dict(sha256=digest(base),
		description='Pre-indoor base feed from the complete upstream source build; retained for independent indoor rebuilding.'), indent=2))
	(payload / 'requirements-indoor.txt').write_text('requests==2.34.2\npyogrio==0.13.0\npyproj==3.8.0\nshapely==2.1.2\n')
	app = (target / 'server/app.py').read_text()
	marker = '\t\t\t\tuid = s["user_id"]\n'
	block = '''\t\t\t\tif path.startswith("/api/indoor/"):
\t\t\t\t\ttry:
\t\t\t\t\t\treturn self.send(routing.router.indoor_data(path))
\t\t\t\t\texcept (KeyError, FileNotFoundError):
\t\t\t\t\t\treturn self.send({"error": "Indoor dataset not found"}, 404)
'''
	if block not in app:
		if app.count(marker) != 1:
			raise ValueError('Native authenticated GET handler changed; review its endpoint mount')
		app = app.replace(marker, marker + block)
	app_path = payload / 'server/app.py'
	app_path.parent.mkdir(parents=True, exist_ok=True)
	app_path.write_text(app)
	rows = []
	for p in sorted(payload.rglob('*')):
		if p.is_file():
			name = p.relative_to(payload).as_posix()
			rows.append(dict(path=name, sha256=digest(p), previous_sha256=digest(target / name)))
	(output / 'manifest.json').write_text(json.dumps(dict(format=1, files=rows), indent=2))
	(output / 'apply.py').write_text(INSTALLER)
	print('Prepared', len(rows), 'files:', output)


if __name__ == '__main__':
	p = argparse.ArgumentParser(description=__doc__)
	p.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
	p.add_argument('--target', type=Path, required=True)
	p.add_argument('--output', type=Path, required=True)
	a = p.parse_args()
	package(a.root.resolve(), a.target.resolve(), a.output.resolve())
