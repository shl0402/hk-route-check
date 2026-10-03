import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('transit_arrivals', Path(__file__).resolve().parents[1] / 'route_checker/transit_arrivals.py')
arrivals = importlib.util.module_from_spec(spec)
spec.loader.exec_module(arrivals)
NOW = datetime.fromisoformat('2026-10-03T21:00:00+08:00')


def match(operator='KMB'):
    return dict(operator=operator, evidence='official_full_stop_sequence', route='98D',
                direction='O', service_type='1')


VISIT = dict(native_stop_id='75E1777F474658CA', seq=1)


def payload(**updates):
    row = dict(route='98D', dir='O', service_type=1, seq=1, co='KMB',
               eta='2026-10-03T21:06:00+08:00', data_timestamp=NOW.isoformat(),
               rmk_en='', rmk_tc='')
    row.update(updates)
    return dict(generated_timestamp=NOW.isoformat(), data=[row])


class ArrivalTests(unittest.TestCase):
    def setUp(self):
        arrivals._cache.clear()

    def normalize(self, p):
        return arrivals.normalize_response(p, arrivals._identity(match(), VISIT), now=NOW)

    def test_exact_kmb_route_direction_variant_and_occurrence(self):
        for changed in ({'route': '98C'}, {'dir': 'I'}, {'service_type': 2}, {'seq': 3}):
            self.assertEqual(self.normalize(payload(**changed))['departures'], [])
        result = self.normalize(payload())
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['departures'][0]['clock'], '21:06')
        self.assertEqual(result['departures'][0]['minutes'], 6)

    def test_stale_generation_and_record_timestamps(self):
        p = payload()
        p['generated_timestamp'] = (NOW-timedelta(minutes=10)).isoformat()
        self.assertEqual(self.normalize(p)['status'], 'stale')
        self.assertEqual(self.normalize(payload(data_timestamp=(NOW-timedelta(minutes=10)).isoformat()))['status'], 'stale')
        self.assertEqual(self.normalize(payload(data_timestamp='2026-10-03T21:00:00'))['departures'], [])

    def test_past_or_far_future_prediction_is_not_displayed(self):
        for seconds in (-60, 4*3600):
            self.assertEqual(self.normalize(payload(eta=(NOW+timedelta(seconds=seconds)).isoformat()))['departures'], [])

    def test_scheduled_is_not_labelled_vehicle_prediction(self):
        result = self.normalize(payload(rmk_en='Scheduled Bus'))
        self.assertEqual(result['departures'][0]['kind'], 'scheduled')

    def test_citybus_filters_occurrence_and_reports_variant_limitation(self):
        m = match('CTB'); m.update(route='N796', service_type=None)
        identity = arrivals._identity(m, dict(native_stop_id='003329', seq=1))
        p = payload(route='N796', co='CTB')
        p['data'] += [dict(p['data'][0], seq=63, eta='2026-10-03T22:30:00+08:00')]
        result = arrivals.normalize_response(p, identity, now=NOW)
        self.assertEqual(len(result['departures']), 1)
        self.assertIn('variants', result['warnings'][0])

    def test_minibus_checks_returned_stop_id_and_disabled_state(self):
        m = match('GMB'); m.update(route='112M', direction='1', native_route_id=2001684)
        identity = arrivals._identity(m, dict(native_stop_id='20017482', seq=1))
        p = dict(generated_timestamp=NOW.isoformat(), data=dict(stop_id=20017482, enabled=True,
                    eta=[dict(timestamp='2026-10-03T21:07:00+08:00', remarks_en='Scheduled')]))
        result = arrivals.normalize_response(p, identity, now=NOW)
        self.assertEqual(result['departures'][0]['clock'], '21:07')
        p['data']['stop_id'] = 20000000
        self.assertEqual(arrivals.normalize_response(p, identity, now=NOW)['departures'], [])
        p['data'].update(stop_id=20017482, enabled=False)
        self.assertEqual(arrivals.normalize_response(p, identity, now=NOW)['departures'], [])

    def test_unverified_or_arbitrary_url_input_never_calls_network(self):
        with patch.object(arrivals, '_request_json') as request:
            self.assertEqual(arrivals.fetch_arrivals(dict(match(), evidence='hkbus_only'), VISIT)['status'], 'invalid_identity')
            self.assertEqual(arrivals.fetch_arrivals(dict(match(), route='../../anything'), VISIT)['status'], 'invalid_identity')
            request.assert_not_called()

    def test_cache_and_unavailable_errors(self):
        with patch.object(arrivals, '_request_json', side_effect=TimeoutError) as request:
            first = arrivals.fetch_arrivals(match(), VISIT)
            second = arrivals.fetch_arrivals(match(), VISIT)
            self.assertEqual(first['status'], 'unavailable')
            self.assertTrue(second['cached'])
            self.assertEqual(request.call_count, 1)

    def test_visit_must_match_verified_route_occurrence(self):
        m = dict(match(), stop_visits=[dict(VISIT, seq=2)])
        with self.assertRaises(ValueError):
            arrivals._identity(m, VISIT)

    def circular(self):
        visit = dict(native_stop_id='001503', seq=45, official_direction='I',
                     official_sequence=27, official_pattern_key='CTB:N796:I')
        m = dict(match('CTB'), route='N796', direction='OI', service_type=None,
                 official_pattern_keys=['CTB:N796:O', 'CTB:N796:I'], stop_visits=[visit])
        return m, visit

    def test_circular_gtfs_occurrence_uses_source_direction_and_sequence(self):
        m, visit = self.circular()
        identity = arrivals._identity(m, visit)
        self.assertEqual(identity['gtfs_seq'], 45)
        self.assertEqual(identity['seq'], 27)
        self.assertEqual(identity['direction'], 'I')
        p = payload(route='N796', co='CTB', dir='I', seq=27)
        # Same physical stop can have other visits/bounds; only I/27 is ours.
        p['data'] += [dict(p['data'][0], seq=45, eta='2026-10-03T21:20:00+08:00'),
                      dict(p['data'][0], dir='O', eta='2026-10-03T21:30:00+08:00')]
        result = arrivals.normalize_response(p, identity, now=NOW)
        self.assertEqual(len(result['departures']), 1)
        self.assertEqual(result['departures'][0]['clock'], '21:06')

    def test_circular_override_cannot_escape_verified_component_or_visit(self):
        m, visit = self.circular()
        for changed in ({'official_pattern_key': 'CTB:N796:O'},
                        {'official_sequence': 45}, {'official_direction': 'O'}):
            with self.assertRaises(ValueError):
                arrivals._identity(m, dict(visit, **changed))
        m['official_pattern_keys'] = ['CTB:N796:O']
        with self.assertRaises(ValueError):
            arrivals._identity(m, visit)

    def test_malformed_upstream_is_unavailable(self):
        for data in (None, [], {'data': []}, {'generated_timestamp': NOW.isoformat(), 'data': 'bad'}):
            self.assertNotEqual(self.normalize(data)['status'], 'ok')


if __name__ == '__main__':
    unittest.main()
