"""Route-reference validation and replay; no network required."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('transit_published', ROOT / 'scripts/transit_enrichment/published.py')
published = importlib.util.module_from_spec(spec)
spec.loader.exec_module(published)


def xml(timing='69', route_id='8545'):
    return ('<dataroot><ROUTE><ROUTE_ID>'+route_id+'</ROUTE_ID><COMPANY_CODE>CTB</COMPANY_CODE>'
            '<ROUTE_NAMEE>N796</ROUTE_NAMEE><ROUTE_TYPE>1</ROUTE_TYPE><SERVICE_MODE>N</SERVICE_MODE>'
            '<SPECIAL_TYPE>0</SPECIAL_TYPE><JOURNEY_TIME>'+timing+'</JOURNEY_TIME>'
            '<LOC_START_NAMEE>LOHAS Park</LOC_START_NAMEE><LOC_END_NAMEE>Tsim Sha Tsui (Circular)</LOC_END_NAMEE>'
            '<LAST_UPDATE_DATE>2026-01-30T00:00:00</LAST_UPDATE_DATE></ROUTE></dataroot>').encode()


class PublishedSourceTests(unittest.TestCase):
    def fixture(self, root):
        directory = published.raw_directory(root)
        directory.mkdir(parents=True)
        files = {}
        for name, url in published.SOURCES.items():
            content = xml() if name.endswith('.xml') else b'%PDF-1.4\nfixture'
            (directory / name).write_bytes(content)
            files[name] = dict(url=url, sha256=published.sha(content), bytes=len(content),
                retrieved_at='2026-10-03T12:00:00Z', last_modified='Wed, 30 Sep 2026 10:03:40 GMT')
        published.save(directory / 'manifest.json', dict(format=1, files=files, complete=True))
        return directory

    def test_identity_retained_and_unit_never_invented(self):
        records, quality = published.parse_routes(xml())
        record = records['8545']
        self.assertEqual(record['route_names']['en'], 'N796')
        self.assertEqual(record['journey_time_value'], 69)
        self.assertIsNone(record['journey_time_unit'])
        self.assertIsNone(record['journey_minutes'])
        self.assertFalse(record['can_replace_gtfs_timing'])
        self.assertEqual(record['destinations']['en'], 'Tsim Sha Tsui (Circular)')
        self.assertEqual(quality['positive_journey_values'], 1)

    def test_missing_or_malformed_time_does_not_become_zero(self):
        for value in ['', '0', '-15', '1.5', '69 min', 'NaN']:
            records, _ = published.parse_routes(xml(value))
            self.assertIsNone(records['8545']['journey_time_value'])
            self.assertIsNone(records['8545']['journey_minutes'])

    def test_rejects_ambiguous_identity_and_duplicate_fields(self):
        duplicate_row = xml().replace(b'</dataroot>', xml().split(b'<dataroot>')[1])
        duplicate_field = xml().replace(b'</ROUTE>', b'<JOURNEY_TIME>108</JOURNEY_TIME></ROUTE>')
        for content in [duplicate_row, duplicate_field, xml(route_id='../a'), xml().replace(b'<ROUTE_TYPE>1', b'<ROUTE_TYPE>2')]:
            with self.assertRaises(ValueError):
                published.parse_routes(content)

    def test_rejects_malformed_external_entities_and_empty_root(self):
        for content in [b'<dataroot>', b'<dataroot/>', b'<html>unavailable</html>',
                        b'<!DOCTYPE data [<!ENTITY x SYSTEM "file:///etc/passwd">]>'+xml(),
                        xml().replace(b'<COMPANY_CODE>CTB</COMPANY_CODE>', b'')]:
            with self.assertRaises(ValueError):
                published.parse_routes(content)

    def test_verified_replay_preserves_snapshot_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fixture(tmp)
            with patch('requests.get', side_effect=AssertionError('Network attempted')):
                result = published.normalize(tmp)
                published.fetch(tmp)
            self.assertEqual(result['routes']['8545']['journey_time_value'], 69)
            self.assertEqual(result['source']['last_modified'], 'Wed, 30 Sep 2026 10:03:40 GMT')
            self.assertEqual(result['source']['sha256'], published.sha(xml()))

    def test_tampered_cache_and_url_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.fixture(tmp)
            (directory / 'ROUTE_BUS.xml').write_bytes(xml('108'))
            with self.assertRaisesRegex(ValueError, 'checksum'):
                published.normalize(tmp)
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.fixture(tmp)
            mp = directory / 'manifest.json'
            data = json.loads(mp.read_text())
            data['files']['ROUTE_BUS.xml']['url'] = 'https://example.org/ROUTE_BUS.xml'
            published.save(mp, data)
            with self.assertRaisesRegex(ValueError, 'URL'):
                published.normalize(tmp)

    def test_partial_cache_fails_without_network_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.fixture(tmp)
            (directory / 'citybus-n796-extension.pdf').unlink()
            with patch('requests.get', side_effect=AssertionError('Network attempted')):
                with self.assertRaisesRegex(ValueError, 'Missing'):
                    published.fetch(tmp, offline=True)
            with self.assertRaisesRegex(ValueError, 'offline'):
                published.fetch(tmp, offline=True, refresh=True)


if __name__ == '__main__':
    unittest.main()
