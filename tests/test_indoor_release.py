"""Release checks protect existing server work and reject corrupted bundles."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import package_indoor_release as release


class IndoorReleaseTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.root = Path(self.temp.name)
		self.bundle, self.target = self.root / 'bundle', self.root / 'app'
		self.source = self.bundle / 'payload/route_checker/server.py'
		self.dest = self.target / 'route_checker/server.py'
		self.source.parent.mkdir(parents=True)
		self.dest.parent.mkdir(parents=True)
		self.source.write_text('new routing')
		self.dest.write_text('old routing')
		self.row = dict(path='route_checker/server.py', sha256=release.digest(self.source), previous_sha256=release.digest(self.dest))
		(self.bundle / 'manifest.json').write_text(json.dumps(dict(files=[self.row])))
		self.module = {'__name__': 'release_test'}
		exec(compile(release.INSTALLER, '<generated installer>', 'exec'), self.module)

	def test_newer_target_is_not_overwritten(self):
		self.dest.write_text('someone else changed this')
		with self.assertRaisesRegex(ValueError, 'Changed file'):
			self.module['install'](self.bundle, self.target, [])
		self.assertEqual(self.dest.read_text(), 'someone else changed this')

	def test_corrupted_bundle_is_rejected(self):
		self.source.write_text('corrupted')
		with self.assertRaises(ValueError):
			self.module['install'](self.bundle, self.target, [])
		self.assertEqual(self.dest.read_text(), 'old routing')

	def test_success_keeps_recoverable_backup(self):
		self.module['install'](self.bundle, self.target, [])
		self.assertEqual(self.dest.read_text(), 'new routing')
		backups = list((self.target / 'data/routing_backups').glob('*/route_checker/server.py'))
		self.assertEqual(len(backups), 1)
		self.assertEqual(backups[0].read_text(), 'old routing')

	def test_check_is_read_only(self):
		self.module['install'](self.bundle, self.target, [], check=True)
		self.assertEqual(self.dest.read_text(), 'old routing')
		self.assertFalse((self.target / 'data/routing_backups').exists())

	def test_payload_symlink_escape_is_rejected(self):
		outside = self.root / 'outside'
		outside.write_text('new routing')
		self.source.unlink()
		self.source.symlink_to(outside)
		with self.assertRaisesRegex(ValueError, 'escapes root'):
			self.module['install'](self.bundle, self.target, [])


if __name__ == '__main__':
	unittest.main()
