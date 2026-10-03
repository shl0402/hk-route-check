"""Guard official data semantics and verified offline cache replay."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from transit_enrichment import official

class OfficialSemantics(unittest.TestCase):
    def band(self,**change):
        h=dict(weekdays=[True]*5+[False,False],public_holiday=False,headway_seq=1,
               start_time='06:50:00',end_time='09:00:00',frequency=12,frequency_upper=15)
        h.update(change); return h

    def test_ranges_are_preserved_without_midpoint(self):
        h=official.normalize_headway(self.band())
        self.assertEqual(h['kind'],'frequency')
        self.assertEqual((h['frequency_seconds'],h['frequency_upper_seconds']),(720,900))
        self.assertEqual(h['start_seconds'],24600)
        self.assertFalse(h['public_holiday'])
        self.assertEqual(h['weekdays'],[True]*5+[False,False])
        self.assertNotIn('headway_secs',h)

    def test_single_departure_requires_explicit_null_fields(self):
        h=official.normalize_headway(self.band(end_time=None,frequency=None,frequency_upper=None))
        self.assertEqual(h['kind'],'departure')
        self.assertIsNone(h['end_seconds'])
        same=official.normalize_headway(self.band(end_time='06:50:00',frequency=None,frequency_upper=None))
        self.assertEqual(same['kind'],'departure')
        self.assertIn('one clock',same['normalization_note'])
        with self.assertRaises(ValueError):
            official.normalize_headway(self.band(end_time=None))
        with self.assertRaises(ValueError):
            official.normalize_headway(self.band(end_time='06:50:00'))

    def test_overnight_and_holidays(self):
        h=official.normalize_headway(self.band(start_time='23:30:00',end_time='02:30:00',
                                               weekdays=[False]*7,public_holiday=True))
        self.assertEqual(h['end_seconds'],26*3600+1800)
        self.assertTrue(h['public_holiday'])
        self.assertFalse(any(h['weekdays']))

    def test_malformed_source_never_silently_defaults(self):
        cases=[dict(frequency=0),dict(frequency=None),dict(frequency=True),dict(frequency_upper=10),
               dict(weekdays=[True]*6),dict(weekdays=[1]*7),dict(public_holiday='yes'),
               dict(start_time='25:99:00'),dict(start_time='5:00'),dict(end_time=None,frequency=None,frequency_upper=5)]
        for change in cases:
            with self.subTest(change=change),self.assertRaises(ValueError):
                official.normalize_headway(self.band(**change))

    def test_stop_order_repeated_circular_stop_is_valid(self):
        rows=[dict(seq=3,stop_id='A'),dict(seq=1,stop_id='A'),dict(seq=2,stop_id='B')]
        self.assertEqual([r['stop_id'] for r in official._ordered(rows)],['A','B','A'])
        for rows in ([dict(seq=1),dict(seq=1)], [dict(seq=1),dict(seq=3)], [dict(seq=1)]):
            with self.assertRaises(ValueError): official._ordered(rows)

    def test_coordinates_reject_nan_swapped_and_outside(self):
        self.assertEqual(official._coordinates('22.3','114.2'),(22.3,114.2))
        for lat,lon in [(float('nan'),114),(114,22.3),(23,114)]:
            with self.assertRaises(ValueError): official._coordinates(lat,lon)

    def fixture(self, root, *, disabled=False, invalid_band=False):
        raw=root/'data/transit_enrichment/raw/official'; manifest={}
        names=dict(name_en='Sample Stop',name_tc='示例站')
        route=dict(route='98D',bound='O',orig_en='Origin',orig_tc='起點',dest_en='Destination',dest_tc='終點')
        kh=[dict(route,service_type=str(i)) for i in (1,2)]
        stoprows=[dict(stop='A',lat='22.3',long='114.2',**names),dict(stop='B',lat='22.31',long='114.21',**names)]
        datasets={
            'kmb/routes.json':kh,'kmb/stops.json':stoprows,
            'kmb/route-stops.json':[dict(route='98D',bound='O',service_type=str(v),seq=str(i+1),stop=s) for v in (1,2) for i,s in enumerate(['A','B'])],
            'ctb/routes.json':[dict(route,co='CTB')],
            'ctb/route-stops/98D-outbound.json':[dict(co='CTB',route='98D',dir='O',seq=i+1,stop=s) for i,s in enumerate(['001','002'])],
            'ctb/route-stops/98D-inbound.json':[],
            'gmb/routes.json':dict(routes=dict(NT=['112M'])),
            'gmb/routes/NT-112M.json':[dict(route_id=123,region='NT',route_code='112M',description_en='Normal',directions=[dict(route_seq=1,orig_en='Origin',orig_tc='起點',dest_en='Destination',dest_tc='終點',headways=[self.band(frequency=0 if invalid_band else 12)])])],
            'gmb/route-stops/123-1.json':dict(route_stops=[dict(stop_seq=i+1,stop_id=s,**names) for i,s in enumerate([100,200])]),
        }
        for sid in ['001','002']:datasets['ctb/stops/'+sid+'.json']=dict(stop=sid,lat=22.3,long=114.2,**names)
        for sid in [100,200]:datasets[f'gmb/stops/{sid}.json']=dict(enabled=not disabled,coordinates=dict(wgs84=dict(latitude=22.3,longitude=114.2)))
        for name,data in datasets.items():
            path=raw/name;official.save(path,dict(data=data))
            manifest[name]=dict(url='https://example.org/'+name,sha256=official.sha(path),retrieved_at='2026-10-03T12:00:00+00:00')
        official.save(raw/'manifest.json',manifest)

    def test_normalization_keeps_native_service_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fixture(Path(tmp));data=official.normalize(Path(tmp))
            ps={p['key']:p for p in data['patterns']}
            self.assertEqual(set(ps),{'KMB:98D:O:1','KMB:98D:O:2','CTB:98D:O','GMB:123:1'})
            self.assertEqual(ps['KMB:98D:O:1']['native_stop_ids'],['A','B'])
            self.assertEqual(ps['CTB:98D:O']['service_type'],None)
            self.assertEqual(ps['GMB:123:1']['direction'],'1')
            self.assertTrue(ps['GMB:123:1']['timetable_usable'])

    def test_disabled_stop_and_bad_frequency_are_quarantined(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fixture(Path(tmp),disabled=True);data=official.normalize(Path(tmp))
            self.assertFalse(any(p['operator']=='GMB' for p in data['patterns']))
            self.assertTrue(any('Disabled official stop' in r['reason'] for r in data['validation']['rejected']))
        with tempfile.TemporaryDirectory() as tmp:
            self.fixture(Path(tmp),invalid_band=True);data=official.normalize(Path(tmp))
            g=next(p for p in data['patterns'] if p['operator']=='GMB')
            self.assertFalse(g['timetable_usable'])
            self.assertEqual(len(g['headway_errors']),1)

    def test_verified_cache_replays_offline_and_rejects_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); raw=root/'data/transit_enrichment/raw/official';raw.mkdir(parents=True)
            f=raw/'test.json';f.write_text('{"data": []}')
            official.save(raw/'manifest.json',{'test.json':dict(url='https://example.org/source',sha256=official.sha(f))})
            c=official.Cache(root,offline=True)
            with patch('requests.get',side_effect=AssertionError('Network must not be used')):
                self.assertEqual(c.get('test.json','https://example.org/source'),{'data':[]})
            with self.assertRaises(ValueError):c.get('test.json','https://example.org/other')
            f.write_text('{"data": [123]}')
            with self.assertRaises(ValueError):c.get('test.json','https://example.org/source')
            with self.assertRaises(ValueError):official.verify(root)
            with self.assertRaises(ValueError):c.get('missing.json','https://example.org/missing')
            with self.assertRaises(ValueError):official.Cache(root,offline=True,refresh=True)

if __name__=='__main__':unittest.main()
