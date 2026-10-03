import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import zipfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from transit_enrichment import identity


class IdentityTest(unittest.TestCase):
    def setUp(self):
        self.ids = ['1', '2', '3']
        self.points = [(22.30, 114.10), (22.31, 114.11), (22.32, 114.12)]
        self.data = {'routes': {'123': {'route_id': '123', 'route_short_name': '98D', 'agency_id': 'KMB', 'route_type': '3'}},
            'stops': {sid: {'stop_id': sid, 'stop_lat': str(p[0]), 'stop_lon': str(p[1])} for sid, p in zip(self.ids, self.points)},
            'patterns': {identity.pattern_key('123', self.ids): {'key': identity.pattern_key('123', self.ids),
                'route_id': '123', 'stop_ids': self.ids, 'direction_ids': ['0'], 'trip_count': 2}}}
        self.hk = {'routeList': {'98D+1+A+B': {'route': '98D', 'gtfsId': '123', 'co': ['kmb'],
            'bound': {'kmb': 'O'}, 'serviceType': '1', 'stops': {'kmb': ['A', 'B', 'C']}}},
            'stopList': {sid: {'location': {'lat': p[0], 'lng': p[1]}, 'name': {'en': sid, 'zh': sid}}
                for sid, p in zip(['A', 'B', 'C'], self.points)}}
        self.official = {'key': 'KMB:98D:O:1', 'operator': 'KMB', 'route': '98D', 'direction': 'O',
            'service_type': '1', 'stops': [dict(native_stop_id=sid, lat=p[0], lon=p[1])
                for sid, p in zip(['A', 'B', 'C'], self.points)]}

    def result(self, official=None):
        result = identity.match_patterns(self.data, self.hk, official)
        return next(iter(result['patterns'].values()))

    def test_full_sequence_proves_crosswalk(self):
        match = self.result()['matches'][0]
        self.assertEqual(match['native_stop_ids'], ['A', 'B', 'C'])
        self.assertEqual(match['evidence'], 'hkbus_full_stop_sequence')
        self.assertEqual(match['max_stop_offset_m'], 0)

    def test_official_direct_match_without_hkbus(self):
        self.hk = {'routeList': {}, 'stopList': {}}
        match = self.result([self.official])['matches'][0]
        self.assertEqual(match['official_pattern_key'], 'KMB:98D:O:1')
        self.assertEqual(match['evidence'], 'official_full_stop_sequence')

    def test_wrong_operator_and_route_are_never_nearest_matched(self):
        self.official['operator'] = 'CTB'
        self.official['route'] = '98C'
        self.hk = {'routeList': {}, 'stopList': {}}
        self.assertEqual(self.result([self.official])['matches'], [])

    def test_declared_gtfs_id_is_not_proof(self):
        self.hk['routeList']['98D+1+A+B']['stops']['kmb'].append('C')
        r = self.result()
        self.assertEqual(r['matches'], [])
        self.assertEqual(r['rejections'][0]['candidate_failures'], {'stop_count_mismatch': 1})

    def test_reversed_sequence_rejected(self):
        self.official['stops'].reverse()
        r = self.result([self.official])
        self.assertEqual(r['matches'], [])
        self.assertEqual(r['rejections'][0]['reason'], 'official_sequence_conflict')

    def test_same_path_multiple_service_types_ambiguous(self):
        special = copy.deepcopy(self.official)
        special.update(key='KMB:98D:O:2', service_type='2')
        r = self.result([self.official, special])
        self.assertEqual(r['matches'], [])
        self.assertEqual(r['rejections'][0]['reason'], 'ambiguous_service_variants')

    def test_official_conflict_blocks_old_hkbus(self):
        self.official['stops'][1]['lat'] += .01
        r = self.result([self.official])
        self.assertEqual(r['matches'], [])
        self.assertEqual(r['rejections'][0]['reason'], 'official_sequence_conflict')

    def test_loop_repeat_visits_remain(self):
        pattern = next(iter(self.data['patterns'].values()))
        pattern['stop_ids'] = ['1', '2', '3', '1']
        self.official['stops'].append(self.official['stops'][0].copy())
        match = self.result([self.official])['matches'][0]
        self.assertEqual(match['native_stop_ids'], ['A', 'B', 'C', 'A'])
        self.assertEqual([v['seq'] for v in match['stop_visits']], [1, 2, 3, 4])

    def test_joint_operators_are_preserved(self):
        self.data['routes']['123']['agency_id'] = 'KMB+CTB'
        ctb = copy.deepcopy(self.official)
        ctb.update(key='CTB:98D:O', operator='CTB', service_type=None)
        for stop in ctb['stops']:
            stop['native_stop_id'] += '-ctb'
        r = self.result([self.official, ctb])
        self.assertEqual([m['operator'] for m in r['matches']], ['CTB', 'KMB'])
        self.assertEqual(r['matches'][0]['native_stop_ids'], ['A-ctb', 'B-ctb', 'C-ctb'])

    def test_coordinate_guard_not_loosened_by_average(self):
        self.official['stops'][1]['lat'] += .0006  # One 67m error: reject even when most stops exact.
        self.assertEqual(self.result([self.official])['matches'], [])

    def test_nan_coordinates_rejected(self):
        self.official['stops'][1]['lat'] = float('nan')
        self.assertEqual(self.result([self.official])['matches'], [])

    def test_surface_pattern_key_compatible(self):
        expected = hashlib.sha256(json.dumps(['123', self.ids]).encode()).hexdigest()[:20]
        self.assertEqual(identity.pattern_key('123', self.ids), expected)


    def test_named_operator_bay_can_corroborate_70m_difference(self):
        self.data['stops']['2']['stop_name'] = '[CTB] OTHER STOP|[KMB] SCHOOL'
        self.official['stops'][1].update(lat=22.3106, name_en='SCHOOL (TK468)')
        # One named70m bay offset is accepted; sharing its name with a different
        # operator would not qualify for the extended distance guard.
        self.assertEqual(len(self.result([self.official])['matches']), 1)
        self.data['stops']['2']['stop_name'] = '[CTB] SCHOOL|[KMB] OTHER STOP'
        self.assertEqual(self.result([self.official])['matches'], [])

    def test_partial_names_do_not_corroborate_distant_stops(self):
        self.data['stops']['2']['stop_name'] = '[KMB] SCHOOL NORTH'
        self.official['stops'][1].update(lat=22.3106, name_en='SCHOOL (TK468)')
        self.assertEqual(self.result([self.official])['matches'], [])


    def loop_fixture(self):
        self.data['routes']['123'].update(agency_id='CTB', route_long_name='A - D (CIRCULAR)')
        self.data['stops']['4'] = dict(stop_id='4', stop_lat='22.33', stop_lon='114.13')
        pattern = next(iter(self.data['patterns'].values()))
        pattern['stop_ids'] = ['1', '2', '3', '4', '1']
        a = dict(self.official, key='CTB:98D:O', operator='CTB', service_type=None,
            stops=[dict(s, seq=i) for i, s in enumerate(self.official['stops'], 1)] + [dict(native_stop_id='D', lat=22.33, lon=114.13, seq=4)])
        b = dict(a, key='CTB:98D:I', direction='I', stops=[dict(a['stops'][2], seq=1), dict(a['stops'][3], seq=2), dict(a['stops'][0], seq=3)])
        return [a, b]

    def test_exact_overlapping_operator_directions_prove_loop(self):
        candidates = self.loop_fixture()
        match = self.result(candidates)['matches'][0]
        self.assertEqual(match['native_stop_ids'], ['A', 'B', 'C', 'D', 'A'])
        self.assertEqual(match['circular_proof']['shared_stop_visits'], 2)
        self.assertEqual([v['official_direction'] for v in match['stop_visits']], ['O', 'O', 'O', 'O', 'I'])
        self.assertEqual([v['official_sequence'] for v in match['stop_visits']], [1, 2, 3, 4, 3])
        self.assertEqual(match['official_pattern_keys'], ['CTB:98D:O', 'CTB:98D:I'])

    def test_no_loop_inference_without_explicit_circular_gtfs(self):
        candidates = self.loop_fixture()
        self.data['routes']['123']['route_long_name'] = 'A - D'
        self.assertEqual(self.result(candidates)['matches'], [])

    def test_one_shared_stop_does_not_prove_loop(self):
        candidates = self.loop_fixture()
        candidates[1]['stops'] = candidates[1]['stops'][1:]
        self.assertEqual(self.result(candidates)['matches'], [])

    def test_invalid_official_pattern_does_not_fall_back_to_hkbus(self):
        self.assertEqual(self.result({'patterns': [], 'available_routes': [{'operator': 'KMB', 'route': '98D'}]})['matches'], [])


    def test_absent_from_complete_official_inventory_blocks_fallback(self):
        r = self.result({'patterns': [], 'available_routes': [{'operator': 'KMB', 'route': 'OTHER'}]})
        self.assertEqual(r['matches'], [])
        self.assertEqual(r['rejections'][0]['reason'], 'absent_from_complete_official_inventory')

    def test_uncovered_operator_can_still_use_verified_hkbus(self):
        r = self.result({'patterns': [], 'available_routes': [{'operator': 'CTB', 'route': 'OTHER'}]})
        self.assertEqual(r['matches'][0]['evidence'], 'hkbus_full_stop_sequence')


class GeometryConflictTest(unittest.TestCase):
    def audit(self, divergent=False, missing=False):
        from pyproj import Transformer
        to_grid = Transformer.from_crs('EPSG:4326', 'EPSG:2326', always_xy=True)
        paths = {'S1': [(114.1, 22.3), (114.102, 22.3)],
                 'S2': [(114.1, 22.3), (114.101, 22.301), (114.102, 22.3)] if divergent else [(114.1, 22.3), (114.102, 22.3)]}
        lines = ['shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence,shape_dist_traveled']
        source = {'patterns': {}}
        crosswalk = {'patterns': {}}
        for i, (sid, points) in enumerate(paths.items(), 1):
            distances = [0.]
            grid = [to_grid.transform(*p) for p in points]
            for a, b in zip(grid, grid[1:]):
                distances.append(distances[-1] + ((b[0]-a[0])**2+(b[1]-a[1])**2)**.5)
            for seq, ((lon, lat), distance) in enumerate(zip(points, distances)):
                lines.append(f'{sid},{lat},{lon},{seq},{distance}')
            key = str(i)
            source['patterns'][key] = {'kind': 'straight_line_distance_fallback' if missing and i == 2 else 'csdi_route_distance',
                'shape_id': sid, 'distances_m': [0., distances[-1]]}
            crosswalk['patterns'][key] = {'route_id': key, 'matches': [{'operator': 'KMB', 'native_stop_ids': ['A', 'B']}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'feed.zip'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('shapes.txt', '\n'.join(lines) + '\n')
            return identity.geometry_conflicts(path, crosswalk, source)

    def test_identical_shared_path_allowed(self):
        result = self.audit()
        self.assertEqual(result['statistics']['shared_native_pairs'], 1)
        self.assertEqual(result['vetoes'], {})

    def test_same_stop_pair_different_roads_vetoed(self):
        result = self.audit(divergent=True)
        self.assertIn('KMB:A>B', result['vetoes'])
        self.assertIn(result['vetoes']['KMB:A>B']['reason'], ('different_segment_lengths', 'different_segment_alignments'))

    def test_missing_shared_geometry_is_not_assumed_identical(self):
        result = self.audit(missing=True)
        self.assertEqual(result['vetoes']['KMB:A>B']['reason'], 'shared_pair_has_unverified_geometry')


if __name__ == '__main__':
    unittest.main()
