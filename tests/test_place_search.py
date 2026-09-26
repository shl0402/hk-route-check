import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'route_checker'))
from place_search import NearbyPlaces, osm_description, fingerprint


class PlaceDescriptions(unittest.TestCase):
    def test_station_track_bus_and_estate_are_distinct(self):
        cases = [
            ({'railway': 'station', 'network': 'MTR'}, 'MTR station'),
            ({'railway': 'stop', 'subway': 'yes'}, 'Rail stopping point (on track)'),
            ({'highway': 'bus_stop', 'bus': 'yes', 'minibus': 'yes'}, 'Bus / minibus stop'),
            ({'bus': 'yes', 'public_transport': 'stop_position'}, 'Bus stopping point (on road)'),
            ({'landuse': 'residential'}, 'Residential area'),
            ({'public_transport': 'station', 'amenity': 'bus_station'}, 'Bus interchange'),
        ]
        for tags, expected in cases:
            with self.subTest(tags=tags):
                self.assertEqual(osm_description(tags, 'node')[0], expected)

    def test_address_and_ref_preserved(self):
        kind, details, address = osm_description({'amenity': 'restaurant', 'addr:housenumber': '15-19',
            'addr:street': 'Wellington Street', 'addr:city': 'Central', 'addr:unit': '4', 'level': '0'}, 'node')
        self.assertEqual(kind, 'Restaurant')
        self.assertEqual(address, '15-19 Wellington Street · Central · Unit 4')
        self.assertIn('Level 0', details)
        self.assertEqual(osm_description({'highway': 'bus_stop', 'network': 'KMB', 'ref': 'TK534'}, 'node')[1], 'KMB · Ref TK534')

    def test_missing_address_stays_missing(self):
        self.assertEqual(osm_description({'amenity': 'restaurant'}, 'node')[2], '')

    def test_nearby_is_not_claimed_as_address_or_containing_district(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'places.geojson'
            path.write_text(json.dumps({'features': [{'geometry': {'type': 'Point', 'coordinates': [114.1, 22.3]},
                'properties': {'TYPE': 'MAL', 'ENGLISHNAME': 'A mall', 'E_AREA': 'Neighbourhood', 'GEONAMEID': 42}}]}))
            index = NearbyPlaces(path)
            description, identity = index.describe(22.3001, 114.1)
            self.assertTrue(description.startswith('Near A mall · Neighbourhood (~'))
            self.assertEqual(identity, '42')
            self.assertEqual(index.describe(22.4, 114.1), ('', ''))

    def test_cache_refreshes_when_source_changes(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'source'
            before = fingerprint([path])
            path.write_text('new source')
            self.assertNotEqual(fingerprint([path]), before)

    def test_search_keeps_coincident_records_and_accepts_brand_spacing(self):
        import server
        from unittest.mock import patch
        records = [dict(name='TamJai', kind='Restaurant', search='tamjai central',
                        searchCompact='tamjaicentral', lat=22.28, lon=114.15, id=identity)
                   for identity in ('osm:node:1', 'osm:way:2')]
        with patch.object(server, 'PLACES', records):
            result = server.search('tam jai')
        self.assertEqual([p['id'] for p in result], ['osm:node:1', 'osm:way:2'])
        self.assertNotIn('searchCompact', result[0])


if __name__ == '__main__':
    unittest.main()
