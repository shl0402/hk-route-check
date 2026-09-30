#!/usr/bin/env python3
"""Verify installed routing files exactly, optionally against the current upstream checkout."""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
	if not path.is_file():
		return None
	with path.open('rb') as stream:
		return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify(target, manifest, source=None):
	rows = json.loads(manifest.read_text())['files']
	issues = []
	for row in rows:
		name = Path(row['path'])
		if name.is_absolute() or '..' in name.parts:
			raise ValueError('Invalid manifest path')
		if digest(target / name) != row['sha256']:
			issues.append('Installed file differs: ' + str(name))
		if source and row.get('source_path'):
			origin = Path(row['source_path'])
			if origin.is_absolute() or '..' in origin.parts:
				raise ValueError('Invalid source path')
			if digest(source / origin) != row['sha256']:
				issues.append('Upstream has changed: ' + str(origin))
	if source:
		packaged = {row['path'] for row in rows}
		for module in (source / 'route_checker').glob('*.py'):
			if module.relative_to(source).as_posix() not in packaged:
				issues.append('New upstream module not packaged: ' + module.name)
	if issues:
		raise ValueError('\n'.join(issues))
	print(f'PASS: {len(rows)} installed files match the release' + (' and recorded upstream sources' if source else ''))


if __name__ == '__main__':
	p = argparse.ArgumentParser(description=__doc__)
	p.add_argument('--target', type=Path, default=Path(__file__).resolve().parents[1])
	p.add_argument('--manifest', type=Path, required=True)
	p.add_argument('--source', type=Path)
	a = p.parse_args()
	verify(a.target.resolve(), a.manifest.resolve(), a.source.resolve() if a.source else None)
