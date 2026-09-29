"""Compile independently verified entrances and platform areas into GTFS.

Legacy logical stops remain for uncovered journeys. New physical platform areas
live under their own station parent; no guessed link joins legacy boarding points.
"""
import csv
import hashlib
import io
import json
import re
import zipfile
from collections import defaultdict

from indoor_matching import choose_entrance, platform_evidence, map_platform_codes
from indoor_timing import pathway_seconds


def compile_stations(root, archive, cache, dest, tables, by_floor, venues, report):
	import indoor_network as n
	from indoor_network import landsd
	stops = n.csv_rows(archive, 'stops.txt')
	levels = n.csv_rows(archive, 'levels.txt')
	if any(s['stop_id'].startswith('INDOOR:') for s in stops):
		raise ValueError('Rebuild from the pre-indoor base feed')
	paths = n.csv_rows(archive, 'pathways.txt')
	proof = json.loads(archive.read('mtr_api_provenance.json'))
	outgoing, incoming, evidence = platform_evidence(root)
	report['platform_evidence'] = evidence
	# One immutable journey context per endpoint; trips refer to these journeys.
	contexts = defaultdict(set)
	journey_context = {}
	for key, journey in proof['od_journeys'].items():
		ids, station_ids = journey['stop_ids'], journey['path']
		if len(ids) < 2:
			continue
		ends = [(ids[0], outgoing.get((ids[0], station_ids[1]), ())),
			(ids[-1], incoming.get((ids[-1], station_ids[-2]), ()))]
		journey_context[key] = ends
		for sid, codes in ends:
			contexts[sid].add(codes)
	parents = defaultdict(list)
	for s in stops:
		if s.get('location_type') == '1' and s['stop_id'].startswith('RAIL:MTR:'):
			parents[landsd.namekey(s['stop_name'])].append(s)
	with zipfile.ZipFile(cache / 'igeocom.zip') as z:
		places = json.loads(z.read(next(x for x in z.namelist() if x.endswith('.geojson'))))['features']
	access_by_name = defaultdict(list)
	for f in places:
		p = f['properties']
		if p.get('TYPE') != 'MTA':
			continue
		name = p.get('E_SITENAME')
		if not name:
			match = re.fullmatch(r'(Mass Transit Railway .+ Station)-[^-]+ Access', p.get('ENGLISHNAME') or '')
			name = match.group(1) if match else None
		access_by_name[landsd.namekey(name)].append(f)
	resolved = {}
	for venue in sorted(venues, key=lambda f: f['properties']['venue_id']):
		vp = venue['properties']; vid, name = vp['venue_id'], vp['venue_name_en']
		row = dict(venue_id=vid, name=name, status='withheld', reasons=[], excluded_entrances=[], excluded_platforms=[])
		report['stations'].append(row)
		lv = landsd.features(cache / vid / 'mtr_level_polygon.geojson')
		amenities = landsd.features(cache / vid / 'mtr_amenity_point.geojson')
		floorids = {f['properties'].get('level_floorpolyid') for f in lv} - {None}
		features = {f['properties']['PedestrianRouteID']: f for floor in sorted(floorids) for f in by_floor.get(floor, [])}
		floor_count = len(features)
		for f in tables['PedestrianRoute']['features']:
			alias = f['properties'].get('AliasNameEN') or ''
			if alias == name or alias.startswith(name + ' '):
				features[f['properties']['PedestrianRouteID']] = f
		features = list(features.values())
		row.update(network_segments=len(features), named_station_additional_segments=len(features)-floor_count,
			map_floors=len(floorids), matched_floors=len(floorids & set(by_floor)))
		landsd.save(dest / 'stations' / (vid + '.json'), dict(venue=venue,
			layers={layer: dict(type='FeatureCollection', features=landsd.features(cache / vid / (layer + '.geojson'))) for layer in landsd.LAYERS},
			network=dict(type='FeatureCollection', features=features), attribution='Lands Department, HKSAR Government',
			height_datum='Hong Kong Principal Datum (metres)'))
		edges, graph, rejected = n.make_graph(features)
		row['excluded_segments'] = rejected
		nodes = {c for e in edges for c in (e['a'], e['b'])}
		def node_for(feature):
			c = n.vertex(feature['geometry']['coordinates'])
			if c in nodes:
				return c
			# Millimetre XY differences arise from the independent datum transforms.
			near = [x for x in nodes if abs(x[2]-c[2]) <= .005 and n.horizontal(x,c) <= .25]
			if len(near) == 1:
				row.setdefault('small_coordinate_matches', []).append(dict(amenity_id=feature['properties']['amenity_id'], horizontal_gap_m=round(n.horizontal(near[0],c),4), vertical_gap_m=round(abs(near[0][2]-c[2]),4)))
				return near[0]
			return None
		matches = parents.get(landsd.namekey(name), [])
		if not matches:
			row['reasons'].append('No timetable station with this physical station name')
			continue
		parent_ids = {s['stop_id'] for s in matches}
		row['legacy_parent_stop_ids'] = sorted(parent_ids)
		boarding = [s for s in stops if s.get('parent_station') in parent_ids and s['stop_id'] in contexts]
		platforms = [f for f in amenities if f['properties'].get('amenity_category','').lower() == 'platform']
		bindings, context_units = {}, {}
		for stop in boarding:
			sid, line = stop['stop_id'], stop['stop_id'].rsplit(':',1)[-1]
			named = [f for f in platforms if line in n.named_lines(f['properties'].get('amenity_name_en'))]
			# A single platform unit in a single-line physical station is unambiguous even without line text.
			if not named and len({s['stop_id'].rsplit(':',1)[-1] for s in boarding}) == 1 and len({f['properties'].get('unit_id') for f in platforms}) == 1:
				named = platforms
			units = {f['properties'].get('unit_id') for f in named} - {None}
			for codes in sorted(contexts[sid]):
				candidates = named
				if len(units) != 1:
					# Explicit platform numbers can identify otherwise unnamed AEL platforms.
					candidates = []
					for code in codes:
						found = [f for f in platforms if code in map_platform_codes(f['properties'].get('amenity_name_en'))
							and (not n.named_lines(f['properties'].get('amenity_name_en')) or line in n.named_lines(f['properties'].get('amenity_name_en')))]
						if not found:
							candidates = []; break
						candidates += found
				chosen_units = {f['properties'].get('unit_id') for f in candidates}
				if len(chosen_units) != 1 or None in chosen_units:
					row['excluded_platforms'].append(dict(stop_id=sid, platform_codes=codes, reason='No unique source platform area for operator direction'))
					continue
				unit = next(iter(chosen_units))
				generic = [f for f in platforms if f['properties'].get('unit_id')==unit and (f['properties'].get('amenity_name_en') or '').casefold()=='platform']
				selected = generic[0] if len(generic)==1 else sorted(candidates,key=lambda f:f['properties']['amenity_id'])[0]
				point = node_for(selected)
				if point is None:
					row['excluded_platforms'].append(dict(stop_id=sid, platform_codes=codes, reason='Platform has no network point at its published height'))
					continue
				physical_id = sid + ':AREA:' + unit
				bindings[physical_id] = (stop, selected, point)
				context_units[(sid,codes)] = physical_id
		entrances = {}
		for f in access_by_name.get(landsd.namekey(name), []):
			p = f['properties']; c = landsd.point(f)
			if not c:continue
			sid = 'LANDSD:ACCESS:' + str(p['GEONAMEID'])
			stop = dict(stop_id=sid,stop_name=p['ENGLISHNAME'],stop_lon=c[0],stop_lat=c[1],source_subcat=p.get('SUBCAT'))
			feature, error = choose_entrance(stop,amenities,n.horizontal)
			point = node_for(feature) if feature else None
			if error or point is None:
				row['excluded_entrances'].append(dict(stop_id=sid,name=stop['stop_name'],reason=error or 'No network point at the published entrance height'))
				continue
			entrances['INDOOR:ACCESS:'+vid+':'+str(p['GEONAMEID'])] = (stop,feature,point)
		used, matrix, connected_platforms, connected_entrances = {}, [], set(), set()
		for eid, entrance in entrances.items():
			for pid, platform in bindings.items():
				forward=n.shortest(graph,entrance[2],platform[2]);reverse=n.shortest(graph,platform[2],entrance[2])
				if not forward or not reverse:continue
				connected_platforms.add(pid);connected_entrances.add(eid)
				for a,b,result in ((eid,pid,forward),(pid,eid,reverse)):
					seconds,chosen=result
					used.update((e['id'],e) for e in chosen)
					matrix.append(dict(from_stop_id=a,to_stop_id=b,estimated_seconds=seconds,
						distance_m=round(sum(e['length'] for e in chosen),2),source_segments=sorted({i for e in chosen for i in e.get('source_ids',[e['source_id']])})))
		for eid in sorted(set(entrances)-connected_entrances):
			row['excluded_entrances'].append(dict(stop_id=entrances[eid][0]['stop_id'],name=entrances[eid][0]['stop_name'],reason='No bidirectional source path to any identified platform'))
		for pid in sorted(set(bindings)-connected_platforms):
			row['excluded_platforms'].append(dict(stop_id=pid,reason='No bidirectional source path from a verified entrance'))
		if not connected_platforms:
			row['reasons'].append('No independently matched entrance/platform pair has a complete source path')
			continue
		parent='INDOOR:STATION:'+vid
		stops.append(dict(stop_id=parent,stop_name=name,stop_lon=matches[0]['stop_lon'],stop_lat=matches[0]['stop_lat'],location_type='1'))
		# Unknown lift landing elevations are real network nodes, not invented floors.
		# Use published floor indices, interpolating only the numeric ordering index.
		known=sorted({(round(float(f['properties']['level_z_value']),3),float(f['properties']['level_ordinal'])) for f in lv})
		new_levels={}
		def level_at(c):
			z=c[2]
			if z in new_levels:return new_levels[z]
			exact=[f for f in lv if abs(float(f['properties']['level_z_value'])-z)<=.005]
			lid='INDOOR:LEVEL:'+vid+':'+str(z)
			if exact:
				p=sorted(exact,key=lambda f:f['properties']['level_id'])[0]['properties']
				index=float(p['level_ordinal']); label=p.get('level_name_en') or ''
			else:
				below=[x for x in known if x[0]<z];above=[x for x in known if x[0]>z]
				if below and above:
					a,b=below[-1],above[0];index=a[1]+(z-a[0])/(b[0]-a[0])*(b[1]-a[1])
				else:
					a=min(known,key=lambda x:abs(x[0]-z));index=a[1]+(z-a[0])/3
				label='' # No assertion of signposted floor name.
				row.setdefault('network_only_levels',[]).append(dict(height_hkpd=z,derived_order_index=index))
			levels.append(dict(level_id=lid,level_index=str(index),level_name=label));new_levels[z]=lid
			return lid
		node_ids={}
		for c in sorted({c for e in used.values() for c in(e['a'],e['b'])}):
			sid='INDOOR:'+vid+':'+hashlib.sha256(json.dumps(c).encode()).hexdigest()[:16]
			node_ids[c]=sid
			stops.append(dict(stop_id=sid,stop_name=name+' passage',stop_lat=str(c[1]),stop_lon=str(c[0]),location_type='3',parent_station=parent,level_id=level_at(c)))
		all_bindings={**{k:bindings[k] for k in sorted(connected_platforms)},**{k:entrances[k] for k in sorted(connected_entrances)}}
		for sid,(old,f,c) in sorted(all_bindings.items()):
			is_platform=sid in connected_platforms
			p=f['properties']
			unit_codes=sorted({code for item in platforms if item['properties'].get('unit_id')==p.get('unit_id') for code in map_platform_codes(item['properties'].get('amenity_name_en'))}) if is_platform else []
			label=old['stop_name'] + (' · Platform ' + '/'.join(unit_codes) if unit_codes else ' · Platform area') if is_platform else old['stop_name']
			stops.append(dict(stop_id=sid,stop_name=label,platform_code='/'.join(unit_codes),
				stop_lat=str(c[1]),stop_lon=str(c[0]),location_type='0' if is_platform else '2',parent_station=parent,level_id=level_at(c)))
			paths.append(dict(pathway_id='INDOOR:LINK:'+sid,from_stop_id=sid,to_stop_id=node_ids[c],pathway_mode='1',is_bidirectional='1',length='0',traversal_time='1'))
		for edge in sorted(used.values(),key=lambda e:e['id']):
			paths.append(dict(pathway_id='INDOOR:'+vid+':'+edge['id'],from_stop_id=node_ids[edge['a']],to_stop_id=node_ids[edge['b']],
				pathway_mode=str(edge['mode']),is_bidirectional='1' if edge['both'] else '0',length=f"{n.horizontal(edge['a'],edge['b']):.3f}",traversal_time=str(pathway_seconds(edge))))
		for context,pid in context_units.items():
			if pid in connected_platforms:resolved[context]=pid
		row.update(status='partial' if row['excluded_entrances'] or row['excluded_platforms'] else 'activated',
			connections=matrix,pathways=len(used),parent_stop_id=parent,
			bindings={sid:f['properties']['amenity_id'] for sid,(_,f,_) in all_bindings.items()},
			verified_entrances=len(connected_entrances),verified_platform_areas=len(connected_platforms))
	return stops,levels,paths,proof,journey_context,resolved


def write_stop_times(archive,target,proof,journey_context,resolved):
	mapped,unmapped=0,0
	with archive.open('stop_times.txt') as src,target.open('stop_times.txt','w') as dst:
		reader=csv.DictReader(io.TextIOWrapper(src,encoding='utf-8-sig'))
		stream=io.TextIOWrapper(dst,encoding='utf-8',newline='')
		writer=csv.DictWriter(stream,reader.fieldnames,lineterminator='\n');writer.writeheader()
		for row in reader:
			trip=proof['od_trips'].get(row['trip_id'])
			if trip:
				ends=journey_context.get(trip['journey'],[])
				context=next((c for c in ends if c[0]==row['stop_id']),None)
				pid=resolved.get(context)
				if pid:row['stop_id']=pid;mapped+=1
				else:unmapped+=1
			writer.writerow(row)
		stream.flush();stream.detach()
	return mapped,unmapped
