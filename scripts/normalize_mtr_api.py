#!/usr/bin/env python3
"""Retain all official evidence; derive conservative same-train segment estimates.
These are operator-derived estimates, not measured in-vehicle times. Never put OD
journey totals directly into GTFS segments. No first/last train calendar guesses.
"""

import argparse, collections, json, pathlib


def sid(v):
	return str(int(v))


def minutes(v):
	if v is None or isinstance(v, bool):
		return None
	try:
		return float(v)
	except (ValueError, TypeError):
		return None


def hr_observations(body, line_alias):
	for route in body.get('routes', []):
		if (
			route.get('special')
			or route.get('rules')
			or route.get('routeStatus')
			or route.get('messages')
		):
			continue
		path = route.get('path') or []
		for i in range(1, len(path) - 1):
			prev, a, b = path[i - 1 : i + 2]
			# Exclude origin allowance, interchange arrival/departure and any
			# segment whose previous edge did not ride the same train line.
			if (
				prev.get('linkType') != 'RIDE'
				or a.get('linkType') != 'RIDE'
				or b.get('linkType') not in ('RIDE', 'END')
			):
				continue
			if str(prev.get('lineID')) != str(a.get('lineID')):
				continue
			line = line_alias.get(str(a.get('lineID')))
			x, y = minutes(a.get('time')), minutes(b.get('time'))
			if line and x is not None and y is not None and 0.5 <= y - x <= 20:
				yield f'MTR:{line}:{sid(a["ID"])}>{sid(b["ID"])}', round((y - x) * 60)


def lr_routes(body):
	for route in body.get('routes', []):
		path = route.get('path') or []
		if (
			len(path) < 2
			or route.get('special')
			or route.get('messages')
			or route.get('rules')
		):
			continue
		if (
			any(p.get('linkType') != 'RIDE' for p in path[:-1])
			or path[-1].get('linkType') != 'END'
		):
			continue
		steps = path[0].get('step') or []
		lines = {str(s['lineID']) for s in steps if s.get('lineID')}
		total = minutes(route.get('time'))
		if total is not None and lines:
			yield tuple(sid(p['ID']) for p in path), lines, total


def main():
	p = argparse.ArgumentParser(description=__doc__)
	p.add_argument('--root', type=pathlib.Path, required=True)
	a = p.parse_args()
	out = a.root / 'data/mtr_api'
	inventory = json.loads((out / 'inventory.json').read_text())
	aliases = {str(l['ID']): l['alias'] for l in inventory['HR']['metadata']['lines']}
	observations = collections.defaultdict(list)
	lr = collections.defaultdict(list)
	counts = collections.Counter()
	urls = {}
	with (out / 'journeys.jsonl').open('w') as stream:
		for file in sorted((out / 'raw').glob('*.json')):
			v = json.loads(file.read_text())
			if v.get('status') != 'ok':
				counts['failed_responses'] += 1
				continue
			system = v['system']
			body = v['response']
			counts[system + '_responses'] += 1
			counts['responses_without_routes'] += not bool(body.get('routes'))
			counts['route_alternatives'] += len(body.get('routes', []))
			counts['with_first_last'] += bool(
				body.get('firstTrain') and body.get('lastTrain')
			)
			counts['with_opening_hours'] += bool(body.get('stationOpeningHours'))
			counts['with_fares'] += bool(
				body.get('fares') or any(r.get('fares') for r in body.get('routes', []))
			)
			# Preserve full structured response, including restrictions, fares,
			# platforms and first/last paths, rather than discarding odd formats.
			stream.write(json.dumps(v, ensure_ascii=False) + '\n')
			urls[file.stem] = v['url']
			if system == 'HR':
				for key, seconds in hr_observations(body, aliases):
					observations[key].append((seconds, v['origin'], file.stem))
			else:
				for path, lines, total in lr_routes(body):
					lr[v['origin']].append((path, lines, total, file.stem))
	for origin, items in lr.items():
		index = collections.defaultdict(list)
		for item in items:
			index[item[0]].append(item)
		for path, lines, total, ref in items:
			if len(path) < 3:
				continue
			for prefix, other, prior, pref in index.get(path[:-1], []):
				delta = round((total - prior) * 60)
				if not 30 <= delta <= 1200:
					continue
				for line in lines & other:
					observations[f'LRT:{line}:{path[-2]}>{path[-1]}'].append(
						(delta, origin, ref + '|' + pref)
					)
	accepted = {}
	rejected = {}
	for key, values in sorted(observations.items()):
		# One vote per origin/value: duplicate alternatives/destinations cannot
		# manufacture independent confirmation of a rounded timing.
		origins = collections.defaultdict(set)
		for seconds, origin, ref in values:
			origins[origin].add(seconds)
		unique = {o: next(iter(v)) for o, v in origins.items() if len(v) == 1}
		hist = collections.Counter(unique.values())
		winner, n = hist.most_common(1)[0] if hist else (None, 0)
		if n < 3 or n / max(1, len(origins)) < 0.9:
			rejected[key] = {
				'origins': len(origins),
				'seconds_votes': dict(hist),
				'reason': 'Requires at least 3 origins and 90% consistent evidence',
			}
			continue
		refs = sorted(
			{
				ref
				for seconds, origin, ref in values
				if seconds == winner and unique.get(origin) == winner
			}
		)
		accepted[key] = {
			'seconds': winner,
			'kind': 'OPERATOR_DERIVED_estimate',
			'semantics': 'same_train_cumulative_difference_estimate',
			'source': 'MTR official journey planner',
			'source_urls': sorted({urls[r] for ref in refs for r in ref.split('|')})[
				:5
			],
			'supporting_origins': n,
			'total_origins': len(origins),
			'evidence': refs,
			'reason': 'Consistent differences between same-train cumulative journey estimates; excludes first boarding and interchange edges. Rounded operator estimates, not measured train running times.',
		}
	result = {
		'segments': accepted,
		'rejected_segments': rejected,
		'counts': dict(counts),
		'coverage': {s: inventory[s]['ordered_pairs'] for s in ['HR', 'LR']},
		'policy': 'No raw OD total, guessed boarding deduction, ambiguous interchange allocation or first/last-train calendar is applied.',
	}
	(out / 'normalized.json').write_text(
		json.dumps(result, ensure_ascii=False, indent=2)
	)
	report = [
		'# MTR API results',
		'',
		f"- Successful pairs: {counts['HR_responses']+counts['LR_responses']} / {sum(v['ordered_pairs'] for v in inventory.values())}.",
		f"- MTR: {counts['HR_responses']}; Light Rail: {counts['LR_responses']}.",
		f"- Route alternatives: {counts['route_alternatives']}; responses without routes: {counts['responses_without_routes']}.",
		f"- Responses with first/last trains: {counts['with_first_last']}; fares: {counts['with_fares']}; opening hours: {counts['with_opening_hours']}.",
		f"- Consistent segment estimates: {len(accepted)}; unresolved candidates: {len(rejected)}.",
		'- Segment values remain operator-derived estimates. Raw journey totals include possible waiting.',
		'- GTFS activation is reported separately; collection alone does not change the live route checker.',
		'',
	]
	(out / 'FINDINGS.md').write_text('\n'.join(report))
	print(json.dumps(result['counts']), 'accepted segments', len(accepted))


if __name__ == '__main__':
	main()
