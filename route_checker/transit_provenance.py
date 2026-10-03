"""Expose the active GTFS snapshot's verified evidence, never a newer raw cache."""


def trip_matches(proof, trip_id):
    key = proof.get('trips', {}).get(trip_id)
    return proof.get('identities', {}).get('patterns', {}).get(key, {}).get('matches', [])


def boarding_options(proof, trip_id, gtfs_stop_id):
    options = []
    for i, match in enumerate(trip_matches(proof, trip_id)):
        if match.get('evidence') != 'official_full_stop_sequence': continue
        visits = [s for s in match['stop_visits'] if s['gtfs_stop_id'] == gtfs_stop_id]
        # A repeated stop without an occurrence supplied by OTP cannot safely
        # choose an arrival endpoint. Never guess the first matching visit.
        if len(visits) != 1: continue
        visit=visits[0]
        options.append(dict(operator=match['operator'], route=match['route'], direction=match['direction'],
            serviceType=match.get('service_type'), nativeStopId=visit['native_stop_id'],
            lat=visit['lat'], lon=visit['lon'], name=visit.get('name_en') or visit.get('name_tc'),
            url='/api/transit/arrivals', tripId=trip_id, stopId=gtfs_stop_id,
            match=i, sequence=visit['seq']))
    return options


def resolve_arrival(proof, trip_id, match_index, stop_id, sequence):
    matches=trip_matches(proof, trip_id)
    if not 0 <= match_index < len(matches): raise ValueError('No verified operator for this trip')
    match=matches[match_index]
    if match.get('evidence')!='official_full_stop_sequence': raise ValueError('No official stop-sequence match')
    visits=[s for s in match['stop_visits'] if s['gtfs_stop_id']==stop_id and s['seq']==sequence]
    if len(visits)!=1: raise ValueError('Unknown stop occurrence')
    return match,visits[0]


def enrich(item, leg, proof, trip_id):
    matches=trip_matches(proof,trip_id)
    if matches:
        item['verifiedIdentity']=[dict(operator=m['operator'],route=m['route'],direction=m['direction'],
            serviceType=m.get('service_type'),evidence=m['evidence'],sourceUrl=m['source_url']) for m in matches]
        stop_id=((leg.get('from') or {}).get('stop') or {}).get('gtfsId','').split(':',1)[-1]
        item['liveArrivals']=boarding_options(proof,trip_id,stop_id)
    timetable=proof.get('timetables',{}).get(trip_id)
    if timetable:
        item.update(sourceType='official_gmb',sourceName='Transport Department minibus API',sourceUrl=timetable['source_url'],
            snapshot=timetable.get('snapshot'),officialTimetable=timetable,wikiStatus='',
            timingKind='published_departure_list' if timetable['kind']=='departure' else 'published_headway',
            intervalExplanation=timetable['policy'])
        item['warnings'].extend(timetable['warnings'])
    checks=proof.get('history_checks',{}).get(trip_id, [])
    if checks:
        item['timingChecks']=checks
        if any(c.get('total_conflict') for c in checks):
            item['warnings'].append('Historical ETA-derived total conflicts with the published timing. Existing timing retained for that span.')
    key=proof.get('trips',{}).get(trip_id)
    route_id=proof.get('identities',{}).get('patterns',{}).get(key,{}).get('route_id')
    if route_id in proof.get('published_route_metadata',{}):
        item['publishedRouteMetadata']=proof['published_route_metadata'][route_id]
    historical=proof.get('history',{}).get(trip_id)
    if historical:
        item.update(historicalTiming=historical,timingModelKind='eta_derived_proportions',
            runningTimeSource='Published totals; ETA-derived intermediate estimates',
            runningTimeSourceUrl=historical['source_url'],
            intervalExplanation='Published timing points and total are unchanged. Accepted spans use ETA-derived proportions; other spans keep distance interpolation.')
        item['warnings'].append('Experimental stop-pair estimates combine historical ETA predictions, not measured bus journeys or live traffic.')
    return item
