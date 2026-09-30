#!/usr/bin/env python3
"""Download and normalize operator publications with hashes and offline replay.

KMB district-section predictions are retained as reference evidence, not
misrepresented as exact stop-pair timetables. Ferry range bounds come from the
operator's downloaded HTML, while departures/classes come from its CSV.
"""
import argparse
import base64
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import time
from urllib.parse import urljoin, urlparse, parse_qs
from bs4 import BeautifulSoup

BASE = 'https://www.sunferry.com.hk/'
FERRIES = ('cheungchau', 'muiwo')
SLUGS = {'cheungchau':'cheung-chau', 'muiwo':'mui-wo'}
BBI = 'https://app.kmb.hk/app1933/BBI/bbi_stop.php?id=0019'
NOTICE = BASE+'assets/files/0724_%E6%85%A2%E8%88%B9%E8%BD%89%E5%BF%AB%E8%88%B9_2_%E9%95%B7%E6%B4%B2.pdf'
MUIWO_NOTICE = BASE+'en/sun-ferry/news-update/new-sailing-schedule-forcentral-mui-wo-route/265'
CITYBUS_N796 = 'https://www.citybus.com.hk/en/uploadedFiles/cust_notice/TS-NWFB-N796-N796-N.pdf'


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.part');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)


def fetch(root,offline=False,refresh=False):
    import requests
    raw=root/'data/operators/raw';raw.mkdir(parents=True,exist_ok=True)
    mp=raw/'manifest.json';manifest=json.loads(mp.read_text()) if mp.exists() else {}
    sources={}
    for kind in FERRIES:
        sources[f'{kind}.csv']=BASE+f'eta/timetable/SunFerry_central_{kind}_timetable_eng.csv'
        sources[f'{kind}.html']=BASE+'en/route-and-fare/timetable?route=central-to-'+SLUGS[kind]
    sources['sunferry-spec.pdf']=BASE+'eta/SunFerry_time_table_and_fare_table_dataspec_eng.pdf'
    sources['sunferry-deployment-notice.pdf']=NOTICE
    sources['muiwo-change.html']=MUIWO_NOTICE
    sources['kmb-bbi-0019.html']=BBI
    sources['citybus-n796.pdf']=CITYBUS_N796
    def get(name,url):
        path=raw/name
        if path.exists() and not refresh:
            if manifest.get(name,{}).get('sha256')!=sha(path):raise ValueError('Unverified operator cache: '+name)
            return path
        if offline:raise ValueError('Missing operator cache; run operator_sources.py fetch: '+name)
        response=requests.get(url,timeout=45);response.raise_for_status()
        if not response.content:raise ValueError('Empty operator response: '+url)
        if name.endswith('.pdf') and not response.content.startswith(b'%PDF'):raise ValueError('Invalid PDF: '+name)
        tmp=path.with_suffix(path.suffix+'.part');tmp.write_bytes(response.content);tmp.replace(path)
        manifest[name]=dict(url=url,sha256=sha(path),retrieved_at=datetime.now(timezone.utc).isoformat(),bytes=len(response.content))
        save(mp,manifest);print('Operator source:',name,flush=True)
        time.sleep(.15)
        return path
    for name,url in sources.items():get(name,url)
    for kind in FERRIES:
        page=BeautifulSoup((raw/(kind+'.html')).read_bytes(),'html.parser')
        links=[urljoin(BASE,a['href']) for a in page.select('a[href]')
            if re.search(r'/T_(?:MW|CH)_.*\.pdf$',a['href'])]
        if len(set(links))!=1:raise ValueError('Ferry timetable PDF link changed: '+kind)
        get(kind+'-timetable.pdf',links[0])
    soup=BeautifulSoup((raw/'kmb-bbi-0019.html').read_bytes(),'html.parser')
    discovered=set()
    for link in soup.select('[href]'):
        url=urljoin(BBI,link['href']);parsed=urlparse(url)
        if parsed.netloc=='app.kmb.hk' and parsed.path=='/app1933/BBI/bbi_stop.php':
            id=parse_qs(parsed.query).get('id',[''])[0]
            if re.fullmatch(r'\d{4}',id):discovered.add(id)
    if not discovered:raise ValueError('KMB directory layout not recognized')
    for id in sorted(discovered-{'0019'}):get(f'kmb-bbi-{id}.html',BBI.replace('0019',id))
    return manifest


def ferry_ranges(html):
    text=BeautifulSoup(html,'html.parser').get_text(' ',strip=True)
    if 'Journey Time' not in text:raise ValueError('Missing operator journey time section')
    text=text.split('Journey Time',1)[1].split('See Also',1)[0]
    out={}
    for kind,lo,hi in re.findall(r'(Ordinary|Fast) Ferry\s+About\s+(\d+)\s*[-–]\s*(\d+)\s+minutes',text,re.I):
        out[kind.lower()]=[int(lo)*60,int(hi)*60]
    if not out:raise ValueError('Unrecognized published ferry range')
    return out


def parse_clock(value):
    if value.strip().lower()=='12:00 noon':return 43200
    if value.strip().lower()=='12:00 midnight':return 0
    m=re.fullmatch(r'(\d{1,2}):(\d{2})\s*([ap])\.m\.',value.strip(),re.I)
    if not m:raise ValueError('Unexpected ferry clock: '+value)
    h,minute=int(m[1]),int(m[2]);assert 1<=h<=12 and minute<60
    return ((h%12)+(12 if m[3].lower()=='p' else 0))*3600+minute*60


def ferry_rows(text,kind,ranges):
    result=[]
    for row in csv.DictReader(io.StringIO(text.lstrip('\ufeff'))):
        remark=row['Remark'].strip();period=row['Service Date'].strip()
        if period=='Mondays to Saturdays except public holidays': days=[0,1,2,3,4,5]
        elif period=='Mondays to Fridays except public holidays': days=[0,1,2,3,4]
        elif period=='Saturdays except public holidays': days=[5]
        elif period=='Sundays and public holidays': days=[6,7]
        else:raise ValueError('Unknown ferry service days: '+period)
        # Sun Ferry data specification, page 2: 1 ordinary; 2 weekday fast;
        # 3 Saturday ordinary; 4 weekday ordinary. Empty is fast.
        if remark not in ('','1','2','3','4'):raise ValueError('Unknown vessel remark: '+remark)
        if kind=='cheungchau':
            vessel='ordinary' if remark in ('1','3','4') else 'fast'
            if remark in ('2','4'):days=[0,1,2,3,4]
            elif remark=='3':days=[5]
        else:
            # Mui Wo has different remark codes (specification page 4).
            if remark not in ('','1','2','3'):raise ValueError('Unknown Mui Wo remark')
            vessel='ordinary' if remark in ('1','2') else 'fast'
            if remark=='3':days=[5]
        result.append(dict(direction=row['Direction'].strip(),days=days,
            departure_seconds=parse_clock(row['Service Hour']),vessel=vessel,
            duration_range_seconds=ranges.get(vessel),raw=row))
    if not result:raise ValueError('Empty ferry timetable')
    return result


def html_ferry_rows(html,kind,ranges):
    """Use the current passenger timetable, including per-sailing exceptions.

    The operator HTML has unclosed nested divs: derive headings per slot,
    not by selecting all descendants of each timetable container.
    """
    soup=BeautifulSoup(html,'html.parser');out=[]
    for slot in soup.select('.slot[data-vessel-type]'):
        origin=slot.find_previous('h2').get_text(' ',strip=True)
        day=slot.find_previous('h3').get_text(' ',strip=True)
        if day=='Mondays to Saturdays (Except Public Holidays)':days=[0,1,2,3,4,5]
        elif day=='Sundays & Public Holidays':days=[6,7]
        else:raise ValueError('Unknown ferry HTML service period: '+day)
        description=slot.select_one('.sr-only').get_text(' ',strip=True)
        if 'Only available from Mondays to Fridays except Public Holidays' in description:days=[0,1,2,3,4]
        elif 'Only available on Saturdays except Public Holidays' in description:days=[5]
        elif 'Only available' in description:raise ValueError('Unknown ferry exception: '+description)
        stop='Cheung Chau' if kind=='cheungchau' else 'Mui Wo'
        if origin not in ('From Central','From '+stop):raise ValueError('Unknown ferry origin: '+origin)
        direction=('Central to '+stop) if origin=='From Central' else (stop+' to Central')
        clock=slot.select_one('[aria-hidden=true]').get_text(' ',strip=True)
        if not re.fullmatch(r'\d{2}:\d{2}',clock):raise ValueError('Unknown ferry HTML clock')
        h,m=map(int,clock.split(':'))
        if h>23 or m>59:raise ValueError('Invalid ferry departure')
        vessel=slot['data-vessel-type']
        if vessel not in ranges:raise ValueError('Unknown ferry vessel class')
        extra_pier='Depart from Central Pier No.5' in description
        out.append(dict(direction=direction,days=days,departure_seconds=h*3600+m*60,
            vessel=vessel,duration_range_seconds=ranges[vessel],central_pier=5 if extra_pier or kind=='cheungchau' else 6,
            raw=description))
    keys=[(r['direction'],tuple(r['days']),r['departure_seconds']) for r in out]
    if len(set(keys))!=len(keys) or not out:raise ValueError('Duplicate or missing ferry sailings')
    return out


def kmb_rows(html,url,retrieved_at):
    soup=BeautifulSoup(html,'html.parser');out=[]
    clocks=re.findall(r'更新時間\s*[:：]\s*(\d{1,2}:\d{2})',soup.get_text(' ',strip=True))
    for table in soup.select('table'):
        area=None
        for row in table.select('tr[onclick]'):
            link=re.search(r"https://m4\.kmb\.hk/kmb-ws/share\.php\?parameter=([^']+)",row.get('onclick',''))
            cells=row.find_all('td',recursive=False)
            if not link or len(cells)<4:continue
            try:identity=json.loads(base64.b64decode(link[1],validate=True))
            except (ValueError,TypeError):raise ValueError('Invalid operator service identity')
            if identity.get('action')!='routedetail':continue
            area=cells[0].get_text(' ',strip=True) or area
            time_text=cells[3].get_text(' ',strip=True)
            m=re.fullmatch(r'~\s*(\d+)\s*分',time_text)
            out.append(dict(route=identity.get('r'),bound=identity.get('b'),service_type=identity.get('s'),
                origin_operator_stop_code=identity.get('c'),destination_area_tc=area,
                predicted_seconds=int(m[1])*60 if m else None,raw_time=time_text,
                source_url=url,retrieved_at=retrieved_at,source_update_clocks=clocks,
                usable_for_gtfs=False,verification='Source gives a destination area, not an exact destination stop or validity interval. Prediction cannot be installed as an all-day stop-pair timetable.'))
    if not out:raise ValueError('KMB section table layout not recognized: '+url)
    return out


def normalize(root):
    raw=root/'data/operators/raw';manifest=json.loads((raw/'manifest.json').read_text())
    for name,meta in manifest.items():
        if sha(raw/name)!=meta['sha256']:raise ValueError('Changed operator source: '+name)
    ferry={}
    for kind in FERRIES:
        ranges=ferry_ranges((raw/(kind+'.html')).read_text())
        csv_rows=ferry_rows((raw/(kind+'.csv')).read_text(),kind,ranges)
        rows=html_ferry_rows((raw/(kind+'.html')).read_text(),kind,ranges)
        expanded=lambda rs:{(r['direction'],d,r['departure_seconds']):r['vessel'] for r in rs for d in r['days']}
        old,current=expanded(csv_rows),expanded(rows)
        ferry[kind]=dict(rows=rows,ranges=ranges,source=manifest[kind+'.html'],timing_source=manifest[kind+'.html'],
            csv_comparison=dict(html_only=len(current.keys()-old.keys()),csv_only=len(old.keys()-current.keys()),
                vessel_conflicts=sum(old[k]!=current[k] for k in old.keys()&current.keys()),
                decision='Current passenger timetable selected; CSV retained for comparison, never blended.'),
            effective_date='2026-08-10' if kind=='muiwo' else None)
    sections=[]
    for name,meta in sorted(manifest.items()):
        if name.startswith('kmb-bbi-'):
            sections+=kmb_rows((raw/name).read_text(),meta['url'],meta['retrieved_at'])
    report=dict(format=1,ferries=ferry,kmb_sections=sections,source_manifest=manifest,
        section_verification=dict(records=len(sections),with_prediction=sum(r['predicted_seconds'] is not None for r in sections),
            usable_as_exact_gtfs_stop_pair=0,reason='Checked operator links: route, direction, service and origin code are supplied; destination stop and prediction validity interval are not. No guessed endpoint or all-day use applied.'))
    save(root/'data/operators/normalized.json',report)
    print(json.dumps(dict(ferry_rows={k:len(v['rows']) for k,v in ferry.items()},kmb=report['section_verification']),ensure_ascii=False),flush=True)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['fetch','normalize'])
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--offline',action='store_true');p.add_argument('--refresh',action='store_true')
    a=p.parse_args()
    if a.command=='fetch':fetch(a.root,a.offline,a.refresh)
    normalize(a.root)
