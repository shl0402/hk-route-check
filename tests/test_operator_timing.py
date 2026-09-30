import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import operator_sources as source
import operator_timing as timing


class OperatorTimetableTests(unittest.TestCase):
    def test_nested_html_keeps_each_sailing_once_with_its_own_day(self):
        html='''<h2>From Central</h2><div class="timetable-p2p"><h3>Mondays to Saturdays (Except Public Holidays)</h3>
        <div class="slot" data-vessel-type="ordinary"><span aria-hidden="true">19:30</span><span class="sr-only">Only available on Saturdays except Public Holidays</span></div>
        <h2>From Cheung Chau</h2><div class="timetable-p2p"><h3>Sundays &amp; Public Holidays</h3>
        <div class="slot" data-vessel-type="fast"><span aria-hidden="true">02:20</span><span class="sr-only">Fast Ferry</span></div>'''
        rs=source.html_ferry_rows(html,'cheungchau',{'ordinary':[3300,3600],'fast':[2100,2400]})
        self.assertEqual(len(rs),2)
        self.assertEqual(rs[0]['days'],[5])
        self.assertEqual(rs[1]['days'],[6,7])
        self.assertEqual(rs[1]['direction'],'Cheung Chau to Central')

    def test_muiwo_published_pier_exception(self):
        html='''<h2>From Central</h2><h3>Mondays to Saturdays (Except Public Holidays)</h3>
        <div class="slot" data-vessel-type="fast"><span aria-hidden="true">03:00</span><span class="sr-only">Depart from Central Pier No.5</span></div>'''
        self.assertEqual(source.html_ferry_rows(html,'muiwo',{'fast':[2100,2400]})[0]['central_pier'],5)

    def test_unknown_exception_fails(self):
        html='''<h2>From Central</h2><h3>Mondays to Saturdays (Except Public Holidays)</h3>
        <div class="slot" data-vessel-type="fast"><span aria-hidden="true">03:00</span><span class="sr-only">Only available on event days</span></div>'''
        with self.assertRaises(ValueError):source.html_ferry_rows(html,'muiwo',{'fast':[2100,2400]})

    def test_csv_remark_meanings_differ_between_routes(self):
        csv='Direction,Service Date,Service Hour,Remark\nCentral to Mui Wo,Sundays and public holidays,12:00 noon,2\n'
        a=source.ferry_rows(csv,'muiwo',{'fast':[2100,2400]})[0]
        self.assertEqual((a['vessel'],a['days'],a['departure_seconds']),('ordinary',[6,7],43200))
        self.assertIsNone(a['duration_range_seconds'])

    def test_published_range_and_overnight_clock(self):
        self.assertEqual(source.ferry_ranges('<h2>Journey Time</h2>Fast Ferry About 35 - 40 minutes'),{'fast':[2100,2400]})
        self.assertEqual(timing.clock(25*3600+30*60),'25:30:00')

    def test_holidays_must_be_excluded(self):
        calendars=[]
        for sid,days in [('a',[0,1,2,3,4]),('b',[5]),('c',[0,1,2,3,4,5]),('d',[6])]:
            calendars.append(dict(service_id=sid,**{d:str(int(i in days)) for i,d in enumerate(timing.DAYS)}))
        ex=[dict(service_id='d',date='20261001',exception_type='1')]
        with self.assertRaises(ValueError):timing.service_map(calendars,ex,{'a','b','c','d'})
        ex += [dict(service_id=s,date='20261001',exception_type='2') for s in ['a','c']]
        self.assertEqual(timing.service_map(calendars,ex,{'a','b','c','d'})[(6,7)],'d')

if __name__=='__main__':unittest.main()
