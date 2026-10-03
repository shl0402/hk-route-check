"""Checks for experimental source validation; no live network needed."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('transit_history', ROOT / 'scripts/transit_enrichment/history.py')
history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(history)


class HistoricalSourceTests(unittest.TestCase):
    def test_rejects_implausible_and_nonfinite_estimates(self):
        values, quality = history.parse_pairs({'a': {'b': 120.5, 'c': -1, 'd': 26469,
                                                    'e': float('nan'), 'f': True, 'a': 60}})
        self.assertEqual(values, {'a': {'b': 120.5}})
        self.assertEqual(quality['accepted_pairs'], 1)
        self.assertEqual(quality['rejected_pairs'], 5)

    def test_schema_change_is_not_silently_consumed(self):
        for bad in ({}, [], {'route': [15, 25]}, {'../a': {'b': 60}}):
            with self.assertRaises(ValueError):
                history.parse_pairs(bad)

    def test_missing_segment_never_becomes_zero_duration(self):
        evidence = history.route_coverage(['a', 'b', 'c'], {'a': {'b': 60}})
        self.assertEqual(evidence['covered'], 1)
        self.assertIsNone(evidence['total_seconds'])
        self.assertEqual(evidence['missing_pairs'], [['b', 'c']])

    def test_direction_is_preserved(self):
        data = {'a': {'b': 60}, 'b': {'a': 95}}
        self.assertEqual(history.route_coverage(['a', 'b'], data)['total_seconds'], 60)
        self.assertEqual(history.route_coverage(['b', 'a'], data)['total_seconds'], 95)

    def test_feeder_variant_stop_identifier(self):
        values, quality = history.parse_pairs({'K53*-U030': {'K53*-U040': 120}})
        self.assertEqual(values['K53*-U030']['K53*-U040'], 120)
        self.assertEqual(quality['rejected_pairs'], 0)

    def fixture(self, root):
        directory = history.raw_directory(root)
        files = {}
        for name, content in [('times/all.json', b'{"a":{"b":120}}'),
                              ('times_hourly/0/03/all.json', b'{"a":{"b":150}}')]:
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            files[name] = dict(sha256=history.sha(content), git_blob_sha=history.git_sha(content),
                               bytes=len(content), retrieved_at='2026-10-03T12:00:00Z', url='https://example.org/' + name)
        manifest = dict(format=1, files=files, source_code_commit=history.REVIEWED_CODE_COMMIT,
                        complete=True, repository=history.SOURCE_URL, schema=history.SCHEMA,
                        data_commit='a'*40, data_commit_date='2026-10-03T12:00:00Z',
                        day_convention='0=Sunday or holiday', hour_timezone='Asia/Hong_Kong',
                        license='GPL-3.0', policy=history.POLICY)
        history.save(directory / 'manifest.json', manifest)
        return directory

    def test_lazy_hour_loading_and_unavailable_hours(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            data = history.normalize(Path(tmp))
            self.assertEqual(data['overall']['a']['b'], 120)
            self.assertEqual(data['hourly'], {})
            self.assertIn('0/03', data['hourly_files'])
            self.assertEqual(history.load_hour(Path(tmp), 0, 3)[0]['a']['b'], 150)
            self.assertEqual(history.load_hour(Path(tmp), 1, 3)[0], {})
            with self.assertRaises(ValueError):
                history.load_hour(Path(tmp), 7, 24)

    def test_cache_tampering_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.fixture(Path(tmp))
            (directory / 'times/all.json').write_text('{"a":{"b":999}}')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                history.normalize(Path(tmp))

    def test_unknown_estimator_version_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.fixture(Path(tmp))
            path = directory / 'manifest.json'
            data = json.loads(path.read_text())
            data['source_code_commit'] = 'b'*40
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'unreviewed'):
                history.normalize(Path(tmp))

    def test_source_audit_cache_migrates_to_text_without_changing_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            content = b'upstream source audit only\n'
            (directory / 'source').mkdir()
            (directory / 'source/main.py').write_bytes(content)
            meta = dict(bytes=len(content), sha256=history.sha(content), git_blob_sha=history.git_sha(content))
            manifest = dict(files={'source/main.py': meta})
            self.assertTrue(history.migrate_audit_text(directory, manifest))
            self.assertFalse((directory / 'source/main.py').exists())
            self.assertEqual((directory / 'source/main.py.txt').read_bytes(), content)
            self.assertEqual(manifest['files'], {'source/main.py.txt': meta})
            self.assertFalse(history.migrate_audit_text(directory, manifest))


if __name__ == '__main__':
    unittest.main()
