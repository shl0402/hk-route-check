"""Source-only regression checks for safe, portable cache/build management."""

import hashlib, io, json, sys, tarfile, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run


class SetupTests(unittest.TestCase):
	def test_landsd_cache_allowlist(self):
		self.assertTrue(run.allowed_cache('data/landsd/raw/igeocom.zip'))
		self.assertTrue(run.allowed_cache('data/landsd/raw/venue/mtr_level_polygon.geojson'))
		self.assertTrue(run.allowed_cache('data/landsd/raw/manifest.json'))
		self.assertFalse(run.allowed_cache('data/landsd/raw/script.py'))
		self.assertFalse(run.allowed_cache('data/landsd/raw/unexpected.zip'))
		self.assertFalse(run.allowed_cache('data/landsd/raw/../../outside.geojson'))
	def archive(self, path, files, hash_override=None):
		meta = {
			'format': 1,
			'files': {
				k: {'bytes': len(v), 'sha256': hashlib.sha256(v).hexdigest()}
				for k, v in files.items()
			},
		}
		if hash_override:
			meta['files'][hash_override]['sha256'] = '0' * 64
		with tarfile.open(path, 'w:gz') as tar:
			for name, body in {
				'cache-manifest.json': json.dumps(meta).encode(),
				**files,
			}.items():
				info = tarfile.TarInfo(name)
				info.size = len(body)
				tar.addfile(info, io.BytesIO(body))

	def test_round_trip_raw_cache(self):
		with tempfile.TemporaryDirectory() as d, patch.object(run, 'ROOT', Path(d)):
			f = Path(d) / 'data/mtr_api/raw/HR_6_31.json'
			f.parent.mkdir(parents=True)
			f.write_text('{"response": {"duration": 22}}')
			# cache_files normally captures module ROOT in its default; specify ROOT in exporter.
			archive = Path(d) / 'cache.tar.gz'
			run.export_cache(archive)
			expected = f.read_bytes()
			f.unlink()
			run.import_cache(archive)
			self.assertEqual(f.read_bytes(), expected)

	def test_bad_hash_does_not_replace_existing_cache(self):
		with tempfile.TemporaryDirectory() as d, patch.object(run, 'ROOT', Path(d)):
			name = 'data/mtr_api/raw/HR_6_31.json'
			f = Path(d) / name
			f.parent.mkdir(parents=True)
			f.write_text('original')
			archive = Path(d) / 'bad.tar.gz'
			self.archive(archive, {name: b'changed'}, name)
			with self.assertRaisesRegex(ValueError, 'checksum'):
				run.import_cache(archive)
			self.assertEqual(f.read_text(), 'original')

	def test_path_traversal_is_rejected(self):
		with tempfile.TemporaryDirectory() as d, patch.object(run, 'ROOT', Path(d)):
			archive = Path(d) / 'bad.tar.gz'
			self.archive(archive, {'data/mtr_api/raw/../../outside.json': b'{}'})
			with self.assertRaisesRegex(ValueError, 'Unexpected cache path'):
				run.import_cache(archive)
			self.assertFalse((Path(d) / 'data/outside.json').exists())

	def test_code_is_never_imported_from_cache(self):
		with tempfile.TemporaryDirectory() as d, patch.object(run, 'ROOT', Path(d)):
			archive = Path(d) / 'bad.tar.gz'
			self.archive(archive, {'run.py': b'print("untrusted")'})
			with self.assertRaises(ValueError):
				run.import_cache(archive)

	def test_target_symlink_cannot_escape_root(self):
		with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other, patch.object(
			run, 'ROOT', Path(d)
		):
			(Path(d) / 'data').symlink_to(other, target_is_directory=True)
			archive = Path(d) / 'bad.tar.gz'
			self.archive(archive, {'data/source_manifest.json': b'{}'})
			with self.assertRaisesRegex(ValueError, 'leaves project'):
				run.import_cache(archive)

	def test_bom_government_json(self):
		with tempfile.TemporaryDirectory() as d:
			p = Path(d) / 'gov.json'
			p.write_bytes(b'\xef\xbb\xbf{"features": [1]}')
			self.assertEqual(run.read(p)['features'], [1])

	def test_build_artifact_tampering_fails_check(self):
		with tempfile.TemporaryDirectory() as d, patch.object(run, 'ROOT', Path(d)):
			gen = Path(d) / 'data/generated'
			gen.mkdir(parents=True)
			(gen / 'hk-transit-EXPERIMENTAL.gtfs.zip').write_bytes(b'changed')
			run.save(gen / 'release-manifest.json', {'gtfs_sha256': '0' * 64})
			with self.assertRaisesRegex(ValueError, 'artifact changed'):
				run.check_build()


class ActiveBuildMetadataTests(unittest.TestCase):
	def server(self):
		import importlib.util

		spec = importlib.util.spec_from_file_location(
			'release_checker',
			Path(__file__).resolve().parents[1] / 'route_checker/server.py',
		)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
		return module

	def test_new_download_does_not_relabel_active_graph(self):
		server = self.server()
		with tempfile.TemporaryDirectory() as d, patch.object(server, 'ROOT', Path(d)):
			run.save(
				Path(d) / 'data/source_manifest.json',
				{'td': {'retrieved_at': '2030-01-01T00:00:00Z'}},
			)
			run.save(
				Path(d) / 'data/generated/release-manifest.json',
				{'source_manifest': {'td': {'snapshot': '2026-09-17'}}},
			)
			self.assertEqual(server.snapshot_date('td'), '2026-09-17')

	def test_request_dates_follow_built_window(self):
		server = self.server()
		with tempfile.TemporaryDirectory() as d, patch.object(server, 'ROOT', Path(d)):
			run.save(
				Path(d) / 'data/generated/otp-smoke/build-config.json',
				{
					'transitServiceStart': '2030-01-02',
					'transitServiceEnd': '2030-01-05',
				},
			)
			data = {
				'origin': {'lat': 22.3, 'lon': 114.17},
				'destination': {'lat': 22.31, 'lon': 114.18},
				'modes': [],
				'preference': 'fastest',
				'departure': '2030-01-04T12:00',
			}
			self.assertEqual(server.validate_request(data).year, 2030)
			with self.assertRaisesRegex(ValueError, 'test graph covers'):
				server.validate_request({**data, 'departure': '2030-01-05T00:00'})


if __name__ == '__main__':
	unittest.main()
