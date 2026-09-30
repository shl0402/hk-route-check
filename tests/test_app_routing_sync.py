"""App envelope semantics and release drift checks, without accounts or live servers."""
import ast
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import secrets
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from verify_routing_sync import digest, verify


class AppRoutingSyncTests(unittest.TestCase):
	def adapter(self):
		tree = ast.parse((ROOT / 'scripts/templates/w8g_routing.py').read_text())
		fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'normalize')
		ns = dict(secrets=secrets, datetime=datetime, router=SimpleNamespace(
			HK=timezone.utc, service_window=lambda: (datetime(2026, 9, 17), datetime(2026, 10, 17))))
		exec(compile(ast.Module(body=[fn], type_ignores=[]), '<adapter>', 'exec'), ns)
		return ns['normalize']

	def test_app_duration_includes_wait_at_origin(self):
		result = self.adapter()(dict(legs=[], duration=1200, displayDurationSeconds=1800,
			originWaitSeconds=600), [], '2026-10-01T09:00:00+08:00')
		self.assertEqual(result['duration'], 1800)
		self.assertEqual(result['originWaitSeconds'], 600)

	def test_missing_access_is_exposed_in_existing_availability_field(self):
		result = self.adapter()(dict(legs=[], duration=1200, hasUnverifiedAccess=True,
			accessGaps=[{'distance': 200}], planningWarnings=['Gap']), [], '2026-10-01T09:00:00+08:00')
		self.assertIn('excludes those connections', result['availability']['text'])
		self.assertEqual(result['accessGaps'], [{'distance': 200}])
		self.assertEqual(result['planningWarnings'], ['Gap'])

	def test_legacy_duration_remains_compatible(self):
		result = self.adapter()(dict(legs=[], duration=1200), [], '2026-10-01T09:00:00+08:00')
		self.assertEqual(result['duration'], 1200)
		self.assertNotIn('Warning:', result['availability']['text'])

	def test_verifier_detects_target_drift_and_new_upstream_module(self):
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			source, target = root / 'source', root / 'target'
			for folder in (source, target):
				(folder / 'route_checker').mkdir(parents=True)
				(folder / 'route_checker/server.py').write_text('same')
			manifest = root / 'manifest.json'
			manifest.write_text(json.dumps({'files': [dict(path='route_checker/server.py',
				source_path='route_checker/server.py', sha256=digest(source / 'route_checker/server.py'))]}))
			with redirect_stdout(io.StringIO()):
				verify(target, manifest, source)
			(target / 'route_checker/server.py').write_text('stale')
			with self.assertRaisesRegex(ValueError, 'Installed file differs'):
				verify(target, manifest, source)
			(target / 'route_checker/server.py').write_text('same')
			(source / 'route_checker/new_dependency.py').write_text('new')
			with self.assertRaisesRegex(ValueError, 'New upstream module'):
				verify(target, manifest, source)

if __name__ == '__main__':
	unittest.main()
