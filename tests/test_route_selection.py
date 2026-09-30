import copy
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'route_checker'))
import server
import route_selection as selection


def route(end, station='3', mode='SUBWAY', duration=600, walk=100, path=None):
    return dict(start='2026-09-30T17:00:00+08:00', end=f'2026-09-30T{end}+08:00',
        duration=duration, walkDistance=walk, transfers=0,
        legs=[dict(mode=mode, transitLeg=True, route={'gtfsId':f'1:RAIL:MTR:TKL:PATH:{duration}' if mode=='SUBWAY' else '1:790'},
            mtrPathStopIds=path or ['RAIL:MTR:57:TKL', f'RAIL:MTR:{station}:TWL'],
            **{'from':{'stop':{'gtfsId':'1:RAIL:MTR:57:TKL'},'lat':22.3,'lon':114.2},
               'to':{'stop':{'gtfsId':f'1:RAIL:MTR:{station}:TWL'},'lat':22.3,'lon':114.17}})])


class SelectionTests(unittest.TestCase):
    def test_earliest_arrival_beats_short_late_trip(self):
        early=route('17:50:00',duration=3000)
        late=route('18:10:00',duration=1200,station='64')
        late['start']='2026-09-30T17:50:00+08:00'
        self.assertEqual(selection.select([late,early],'fastest',5,server.timestamp)[0],early)

    def test_five_mtr_routes_beat_bus_without_mode_quota(self):
        rail=[route(f'17:{40+i}:00',station=str(i)) for i in range(5)]
        bus=route('18:00:00',mode='BUS')
        self.assertEqual(selection.select([bus,*rail],'fastest',5,server.timestamp),rail)

    def test_distinct_station_and_internal_path_survive(self):
        a=route('17:40:00');b=route('17:42:00',station='64')
        c=route('17:44:00',path=['RAIL:MTR:57:TKL','RAIL:MTR:64:EAL','RAIL:MTR:3:TWL'])
        duplicate=copy.deepcopy(a);duplicate['end']='2026-09-30T17:48:00+08:00'
        duplicate['legs'][0]['route']['gtfsId']='1:RAIL:MTR:TKL:PATH:1234'
        self.assertEqual(selection.select([duplicate,c,b,a],'fastest',6,server.timestamp),[a,b,c])

    def test_different_ferry_classes_are_distinct_but_departure_copies_are_not(self):
        a=route('18:20:00',mode='FERRY');b=copy.deepcopy(a)
        a['legs'][0]['provenance']={'operatorTiming':{'vessel':'ordinary'}}
        b['legs'][0]['provenance']={'operatorTiming':{'vessel':'fast'}}
        later=copy.deepcopy(b);later['end']='2026-09-30T18:40:00+08:00'
        self.assertEqual(len(selection.select([a,b,later],'fastest',6,server.timestamp)),2)

    def test_walking_and_transfers_use_arrival_as_tie_break(self):
        a=route('17:40:00',walk=300);b=route('17:50:00',station='64',walk=100)
        self.assertEqual(selection.select([a,b],'walking',2,server.timestamp),[b,a])
        self.assertEqual(selection.select([b,a],'transfers',2,server.timestamp),[a,b])

    def test_physical_platforms_group_by_station_with_real_engine_prefix(self):
        stops={'RAIL:MTR:64:EAL:AREA:a':dict(stop_lat='22.3',stop_lon='114.17',location_type='0'),
               'RAIL:MTR:64:TML:AREA:b':dict(stop_lat='22.3',stop_lon='114.17',location_type='0'),
               'RAIL:MTR:3:TWL':dict(stop_lat='22.29',stop_lon='114.17',location_type='0')}
        ids=['graph:'+x for x in stops]
        out=selection.nearby_station_groups({'lat':22.3,'lon':114.17},stops,ids)
        self.assertEqual(out[0]['station'],'64');self.assertEqual(len(out[0]['stops']),2)
        self.assertTrue(all(x.startswith('graph:') for x in out[0]['stops']))

    def test_via_candidates_are_checked_for_actual_alighting(self):
        self.assertTrue(selection.matches_stations(route('17:40:00',station='64'),('57','64')))
        self.assertFalse(selection.matches_stations(route('17:40:00'),('57','64')))

    def test_off_network_pin_reports_gap_without_adding_time(self):
        # Google's published encoded-polyline example has known endpoints.
        geometry='_p~iF~ps|U_ulLnnqC_mqNvxq`@'
        it={'duration':600,'legs':[{'legGeometry':{'points':geometry}}]}
        gaps=selection.endpoint_coverage(it,dict(lat=38.502,lon=-120.2),dict(lat=43.252,lon=-126.453))
        self.assertEqual(len(gaps),1)
        self.assertEqual(gaps[0]['endpoint'],'origin')
        self.assertAlmostEqual(gaps[0]['distanceMetres'],222,delta=1)
        self.assertEqual(it['duration'],600)
        self.assertEqual(selection.endpoint_coverage(it,dict(lat=38.5,lon=-120.2),dict(lat=43.252,lon=-126.453)),[])

    def test_all_waits_and_time_before_leaving_are_counted(self):
        def leg(a,b,transit):
            return dict(mode='BUS' if transit else 'WALK',transitLeg=transit,
                start={'scheduledTime':f'2026-09-30T{a}+08:00'},end={'scheduledTime':f'2026-09-30T{b}+08:00'},
                duration=300,route=None,**{'from':{},'to':{}})
        it=dict(start='2026-09-30T17:10:00+08:00',end='2026-09-30T17:25:00+08:00',duration=900,
                legs=[leg('17:10:00','17:15:00',False),leg('17:20:00','17:25:00',True)])
        with patch.object(server,'enrich_leg'),patch.object(server,'source_context',return_value={}),patch.object(server,'leg_provenance',return_value={}):
            out=server.normalize(it,'2026-09-30T17:00:00+08:00')
        self.assertEqual(out['displayDurationSeconds'],1500)
        self.assertEqual(out['duration'],900)
        self.assertEqual(out['originWaitSeconds'],600)
        self.assertEqual(out['initialWaitSeconds'],300)
        self.assertEqual(out['initialWaitExcludedSeconds'],0)
        self.assertFalse(out['legs'][1]['initialWaitExcluded'])

if __name__=='__main__':unittest.main()
