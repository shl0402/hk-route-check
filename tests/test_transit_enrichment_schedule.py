import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from transit_enrichment.schedule import compile_schedule

class OfficialScheduleTests(unittest.TestCase):
    def row(self,**kw):
        x=dict(kind='frequency',weekdays=[True]*7,public_holiday=True,start_seconds=21600,end_seconds=82800,
               frequency_seconds=720,frequency_upper_seconds=900)
        x.update(kw);return x
    def pattern(self,*rows,**kw):
        return dict(operator='GMB',timetable_usable=True,headway_errors=[],headways=list(rows),**kw)
    def compile(self,p,start='2026-10-01',end='2026-10-03',holidays=('2026-10-01',),years=(2026,)):
        return compile_schedule(p,start,end,holidays,years)
    def test_holiday_replaces_weekday_rules(self):
        ordinary=self.row(weekdays=[True]*5+[False]*2,public_holiday=False)
        holiday=self.row(weekdays=[False]*6+[True],public_holiday=True,frequency_seconds=1200,frequency_upper_seconds=None)
        s=self.compile(self.pattern(ordinary,holiday))
        self.assertTrue(s['usable'],s['reason'])
        self.assertEqual(s['days'][0]['frequencies'][0]['headway_secs'],1200)
        self.assertEqual(s['days'][1]['frequencies'][0]['headway_secs'],900)
        self.assertFalse(s['days'][2]['frequencies'])
    def test_upper_bound_retains_published_range_nonexact(self):
        s=self.compile(self.pattern(self.row()))
        b=s['days'][0]['frequencies'][0]
        self.assertEqual(b['headway_range_seconds'],[720,900]);self.assertEqual(b['headway_secs'],900)
        self.assertEqual(b['exact_times'],0)
    def test_explicit_departures_not_expanded_from_headways(self):
        point=self.row(kind='departure',start_seconds=5*3600,end_seconds=None,frequency_seconds=None,frequency_upper_seconds=None)
        s=self.compile(self.pattern(point,self.row()))
        self.assertTrue(s['usable'],s['reason']);self.assertEqual(s['days'][0]['departures'],[18000])
    def test_conflicts_reject_entire_schedule(self):
        for second in [self.row(start_seconds=30000,frequency_seconds=300),
                       self.row(kind='departure',start_seconds=30000,end_seconds=None,frequency_seconds=None,frequency_upper_seconds=None)]:
            s=self.compile(self.pattern(self.row(),second));self.assertFalse(s['usable']);self.assertEqual(s['days'],[])
    def test_overlapping_identical_bands_coalesce(self):
        s=self.compile(self.pattern(self.row(end_seconds=40000),self.row(start_seconds=30000)))
        self.assertTrue(s['usable'],s['reason']);self.assertEqual(len(s['days'][0]['frequencies']),1)
    def test_overnight_no_fabricated_phase(self):
        s=self.compile(self.pattern(self.row(start_seconds=23*3600,end_seconds=2*3600)))
        self.assertTrue(s['usable'],s['reason']);self.assertEqual(s['days'][0]['frequencies'][0]['end_seconds'],26*3600)
        s=self.compile(self.pattern(self.row(start_seconds=23*3600,end_seconds=2*3600),self.row(start_seconds=3600,end_seconds=3*3600)))
        self.assertFalse(s['usable']);self.assertIn('Overnight',s['reason'])
    def test_source_sequence_continues_exact_midnight_boundary(self):
        # End already normalized by the official parser; source headway_seq proves order.
        late=self.row(headway_seq=5,start_seconds=23*3600,end_seconds=86400+600)
        early=self.row(headway_seq=6,start_seconds=600,end_seconds=3000)
        s=self.compile(self.pattern(late,early))
        self.assertTrue(s['usable'],s['reason'])
        self.assertEqual(s['days'][0]['frequencies'][0]['end_seconds'],86400+3000)
        isolated=self.row(headway_seq=6,start_seconds=1800,end_seconds=3000)
        s=self.compile(self.pattern(late,isolated))
        self.assertTrue(s['usable'],s['reason'])
        self.assertEqual(s['days'][0]['frequencies'][0]['start_seconds'],1800)

    def test_missing_holidays_and_invalid_source_cannot_apply(self):
        s=self.compile(self.pattern(self.row()),years=())
        self.assertFalse(s['usable']);self.assertIn('holiday coverage',s['reason'])
        p=self.pattern(self.row());p['headway_errors']=[{'reason':'malformed band'}]
        self.assertFalse(self.compile(p)['usable'])
        p=self.pattern(self.row());p['timetable_usable']=False
        self.assertFalse(self.compile(p)['usable'])
    def test_school_event_and_amendment_conditions_are_not_weekdays(self):
        for field,text in [('description_tc','星期一至五上課日'),('description_en','Race days only'),
                           ('remarks_en','Temporary suspension from 2026-10-01'),('description_tc','聖誕節特別班次')]:
            p=self.pattern(self.row());p[field]=text
            self.assertFalse(self.compile(p)['usable'])
        for text in ['Holiday Schedule','Special Route (Omit Science and Technology Park)',
                     'Special Departure terminating at Pennington Street']:
            p=self.pattern(self.row());p['description_en']=text
            self.assertTrue(self.compile(p)['usable'])

    def test_zero_width_with_frequency_not_a_departure(self):
        s=self.compile(self.pattern(self.row(start_seconds=3600,end_seconds=3600)))
        self.assertFalse(s['usable']);self.assertIn('Zero-width',s['reason'])

if __name__=='__main__':unittest.main()
