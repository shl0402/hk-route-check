"""Compile source-backed GMB day rules without inventing exact departures.

This helper returns calendar-day clock bands. Only a band whose published end
precedes its start crosses midnight; independent early clocks are not silently
moved to the preceding service day. Conflicting data rejects the complete schedule.
"""
from datetime import date, datetime, timedelta
import re

POLICY = ('Official GMB weekday/public-holiday rules. Published headway ranges use '
          'their upper bound as a conservative frequency model (exact_times=0), '
          'not a list of exact departures. Standalone clock departures stay on '
          'their specified calendar day; crossing-midnight bands and directly contiguous '
          'source-sequence continuations end after 24:00.')


def _date(value):
    if isinstance(value, datetime): return value.date()
    if isinstance(value, date): return value
    value=str(value)
    return datetime.strptime(value, '%Y%m%d' if len(value)==8 else '%Y-%m-%d').date()


def _integer(value, name, minimum=0):
    if isinstance(value,bool) or not isinstance(value,int) or value<minimum:
        raise ValueError('Invalid '+name)
    return value


def _rollover_continuations(selected):
    """Follow a proved midnight boundary in the source presentation sequence.

    A 23:50–00:10 band followed by 00:10–00:50 is one contiguous service
    day. Never roll an isolated early time forward merely because it is early.
    """
    ids=[h.get('headway_seq') for h in selected]
    if any(type(i) is not int for i in ids) or len(ids)!=len(set(ids)):
        return selected
    out=[];previous=None
    for source in sorted(selected,key=lambda h:h['headway_seq']):
        h=dict(source)
        if (previous and previous.get('end_seconds') is not None
                and previous['end_seconds']>=86400
                and h['start_seconds']==previous['end_seconds']%86400):
            h['start_seconds']+=86400
            if h.get('end_seconds') is not None:h['end_seconds']+=86400
            h['verified_overnight_continuation']=True
        out.append(h);previous=h
    return out


def _day_rows(headways, when, holidays):
    holiday=when in holidays
    selected=([h for h in headways if h['public_holiday']] if holiday else
              [h for h in headways if h['weekdays'][when.weekday()]])
    selected=_rollover_continuations(selected)
    points=set();bands=[]
    for h in selected:
        start=_integer(h.get('start_seconds'),'start_seconds')
        if start>=86400 and not (start<172800 and h.get('verified_overnight_continuation')):
            raise ValueError('Source start is outside one calendar day; service-day mapping unresolved')
        if h.get('kind')=='departure':
            if h.get('frequency_seconds') is not None or h.get('frequency_upper_seconds') is not None:
                raise ValueError('Point departure has a frequency')
            points.add(start);continue
        if h.get('kind')!='frequency': raise ValueError('Unknown timetable row kind')
        end=_integer(h.get('end_seconds'),'end_seconds')
        if end<start: end+=86400
        if not start<end<=start+86400: raise ValueError('Zero-width or >24-hour frequency band')
        low=_integer(h.get('frequency_seconds'),'frequency_seconds',1)
        upper=h.get('frequency_upper_seconds')
        high=low if upper is None else _integer(upper,'frequency_upper_seconds',1)
        if high<low:raise ValueError('Headway range is reversed')
        bands.append(dict(start_seconds=start,end_seconds=end,headway_secs=high,
            headway_range_seconds=[low,high],exact_times=0))
    bands.sort(key=lambda x:(x['start_seconds'],x['end_seconds'],x['headway_secs']))
    merged=[]
    for band in bands:
        if merged and band['start_seconds']<=merged[-1]['end_seconds']:
            prev=merged[-1]
            same=band['headway_range_seconds']==prev['headway_range_seconds']
            if band['start_seconds']<prev['end_seconds'] and not same:
                raise ValueError('Overlapping bands have different published headways')
            if same:
                prev['end_seconds']=max(prev['end_seconds'],band['end_seconds']);continue
        merged.append(band)
    if any(b['start_seconds']<=p<b['end_seconds'] for p in points for b in merged):
        raise ValueError('An explicit departure overlaps a frequency band; double counting unresolved')
    return dict(date=when.strftime('%Y%m%d'),public_holiday=holiday,
                departures=sorted(points),frequencies=merged)


def _unencoded_conditions(pattern):
    text=' '.join(str(pattern.get(k) or '') for k in ('description_en','description_tc','remarks_en','remarks_tc'))
    condition=re.compile(
        r'school\s*(?:days?|terms?|holidays?|hours)|after\s*school|school\s*dismissal|'
        r'上課日|上学日|上學日|學校假期|学校假期|放學|放学|'
        r'race\s*days?|racecourse\s*(?:meetings?|events?)|賽馬日|赛马日|'
        r'event\s*(?:days?|only)|during\s+(?:the\s+)?(?:event|festival)|'
        r'活動期間|活动期间|賽事期間|赛事期间|球賽日|球赛日|'
        r'Christmas|(?:Lunar\s+)?New\s+Year|除夕|聖誕|圣诞|年初|中秋|重陽|重阳',re.I)
    if condition.search(text):
        raise ValueError('School/event/named-holiday condition is not encoded by weekday/public-holiday flags')
    remarks=' '.join(str(pattern.get(k) or '') for k in ('remarks_en','remarks_tc'))
    if re.search(r'suspend|resum|cancel|temporar|effective|divert|amend|暫停|暫時|暂停|取消|恢復|恢复|即日起|調整|调整|改道|改經|改经|\d{4}[-/年]\d',remarks,re.I):
        raise ValueError('Service amendment remarks require an explicit validity calendar')


def compile_schedule(pattern,start_date,end_date,holiday_dates,holiday_years):
    """Return {usable, reason, days, warnings, policy}; never partially apply a day.

    ``holiday_dates`` is the complete published Hong Kong holiday date set for
    ``holiday_years``. These arguments are mandatory because ordinary weekdays
    and a public holiday on that weekday must not be conflated.
    """
    result=dict(usable=False,reason=None,days=[],warnings=[],policy=POLICY)
    try:
        start,end=_date(start_date),_date(end_date)
        if start>end:raise ValueError('Schedule date range is reversed')
        holidays={_date(d) for d in holiday_dates};years={int(y) for y in holiday_years}
        required=set(range(start.year,end.year+1))
        if not required<=years:raise ValueError('Published Hong Kong holiday coverage is missing for requested years')
        if pattern.get('operator')!='GMB':raise ValueError('This compiler accepts official GMB schedules only')
        if pattern.get('timetable_usable') is not True or pattern.get('headway_errors'):
            raise ValueError('Official pattern has missing or unverified timetable rows')
        _unencoded_conditions(pattern)
        hs=pattern.get('headways')
        if not isinstance(hs,list) or not hs:raise ValueError('No official timetable rows')
        for h in hs:
            w=h.get('weekdays')
            if not isinstance(w,list) or len(w)!=7 or any(type(x) is not bool for x in w) or type(h.get('public_holiday')) is not bool:
                raise ValueError('Unknown weekday/public-holiday rule')
        days=[];when=start
        while when<=end:
            days.append(_day_rows(hs,when,holidays));when+=timedelta(days=1)
        if not any(d['departures'] or d['frequencies'] for d in days):
            raise ValueError('No source service within requested dates')
        # Check absolute instants as well as same-day clock bands. A previous-day
        # overnight band must not duplicate the next day's early service rows.
        intervals=[];points=[]
        for i,d in enumerate(days):
            offset=i*86400
            intervals.extend((offset+b['start_seconds'],offset+b['end_seconds']) for b in d['frequencies'])
            points.extend(offset+p for p in d['departures'])
        intervals.sort()
        if any(right[0]<left[1] for left,right in zip(intervals,intervals[1:])):
            raise ValueError('Overnight band overlaps next calendar day; source service-day mapping unresolved')
        if any(a<=p<b for p in points for a,b in intervals):
            raise ValueError('An overnight frequency band overlaps a calendar-day departure')
        result.update(usable=True,days=days)
        if any(h.get('normalization_note') for h in hs):
            result['warnings'].append('Some single departures use identical start/end clocks in the API; original fields are retained.')
        if any(h.get('frequency_upper_seconds') is not None and h.get('frequency_seconds')!=h.get('frequency_upper_seconds') for h in hs):
            result['warnings'].append('Published frequency ranges use their upper bound for planning; actual departures vary.')
    except (ValueError,TypeError,KeyError) as e:
        result['reason']=str(e)
    return result
