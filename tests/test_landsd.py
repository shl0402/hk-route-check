import csv, io, json, sys, tempfile, unittest, zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import landsd_enrich as l

class EnrichmentTests(unittest.TestCase):
    def setup_source(self, root, duplicate=False):
        cache=root/'data/landsd/raw';cache.mkdir(parents=True)
        def feature(p,c=None):return dict(type='Feature',properties=p,geometry=dict(type='Point',coordinates=c) if c else None)
        def fc(fs):return dict(type='FeatureCollection',totalFeatures=len(fs),features=fs)
        venue=feature(dict(venue_id='v1',venue_name_en='North Point Station'))
        l.save(cache/'venues.geojson',fc([venue]))
        for layer in l.LAYERS:
            fs=[]
            if layer=='mtr_level_polygon':fs=[feature(dict(level_id='l1',level_ordinal=-1,level_name_en='Concourse',level_name_zh='大堂'))]
            if layer=='mtr_amenity_point':fs=[feature(dict(amenity_category='entry',amenity_name_en='Exit A'),[114.2,22.29,-5]),feature(dict(amenity_id='p1',amenity_category='platform',amenity_name_en='Platform 2 to Central',amenity_name_zh='2號月台',level_id='l1'),[114.21,22.3,-8])]
            l.save(cache/'v1'/ (layer+'.geojson'),fc(fs))
        places=[feature(dict(TYPE='RSN',ENGLISHNAME='Mass Transit Railway North Point Station',CHINESENAME='北角站',GEONAMEID=1,REV_DATE='1/1/2026'),[114.2,22.29]),feature(dict(TYPE='MTA',ENGLISHNAME='Mass Transit Railway North Point Station-A Access',CHINESENAME='北角站A出口',E_SITENAME='Mass Transit Railway North Point Station',GEONAMEID=2,REV_DATE='1/1/2026'),[114.21,22.29])]
        with zipfile.ZipFile(cache/'igeocom.zip','w') as z:z.writestr('poi.geojson',json.dumps(fc(places)))
        l.save(cache/'manifest.json',{p.relative_to(cache).as_posix():dict(sha256=l.sha(p),url='https://example.test/source') for p in cache.rglob('*') if p.is_file()})
        stops=[dict(stop_id='RAIL:MTR:31',stop_name='North Point',stop_lat='22.3',stop_lon='114.2',location_type='1',parent_station=''),dict(stop_id='RAIL:MTR:31:ISL',stop_name='North Point (ISL)',stop_lat='22.3',stop_lon='114.2',location_type='0',parent_station='RAIL:MTR:31')]
        if duplicate:stops.append(dict(stops[0],stop_id='RAIL:MTR:999'))
        src=root/'source.zip'
        with zipfile.ZipFile(src,'w') as z:
            z.writestr('stops.txt',l.csv_bytes(stops,list(stops[0])))
            z.writestr('stop_times.txt',b'unchanged timing evidence\n')
            z.writestr('transfers.txt',b'unchanged transfer evidence\n')
        return src

    def test_preserves_timing_no_invented_paths_or_wheelchair_flag(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);src=self.setup_source(root);dst=root/'out.zip';l.merge(root,src,dst)
            with zipfile.ZipFile(dst) as z:
                stops=list(csv.DictReader(io.StringIO(z.read('stops.txt').decode())))
                entrances=[s for s in stops if s['location_type']=='2'];self.assertEqual(len(entrances),1)
                self.assertEqual(entrances[0]['parent_station'],'RAIL:MTR:31')
                platform=next(s for s in stops if s['stop_id']=='LANDSD:PLATFORM:p1')
                self.assertEqual(platform['level_id'],'LANDSD:LEVEL:l1');self.assertEqual(platform['platform_code'],'2')
                self.assertNotIn('wheelchair_boarding',entrances[0]);self.assertNotIn('pathways.txt',z.namelist())
                self.assertEqual(z.read('stop_times.txt'),b'unchanged timing evidence\n')
                self.assertEqual(next(s for s in stops if s['stop_id'].endswith(':ISL'))['stop_lat'],'22.3')
            dst2=root/'again.zip';l.merge(root,dst,dst2)
            with zipfile.ZipFile(dst) as a,zipfile.ZipFile(dst2) as b:
                self.assertNotIn('translations.txt',a.namelist())
                for name in ['stops.txt','levels.txt','attributions.txt']:self.assertEqual(a.read(name),b.read(name),name)

    def test_ambiguous_parent_withheld(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);src=self.setup_source(root,True);l.merge(root,src,root/'out.zip')
            report=json.loads((root/'data/landsd/merge_report.json').read_text())
            self.assertEqual(report['entrances_added'],0);self.assertEqual(report['station_coordinates_updated'],0)
            self.assertEqual(report['withheld_records'],2)

    def test_truncated_response_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'bad.json';l.save(p,dict(type='FeatureCollection',totalFeatures=5,features=[]))
            with self.assertRaises(ValueError):l.features(p)

    def test_corrupt_cache_fails_offline(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);self.setup_source(root);(root/'data/landsd/raw/venues.geojson').write_text('{}')
            with self.assertRaises(ValueError):l.fetch(root,offline=True)

if __name__=='__main__':unittest.main()
