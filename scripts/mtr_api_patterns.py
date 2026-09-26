"""Use complete, line/path-matched MTR API cumulative timings. No distance fallback."""

import json
from functools import lru_cache


class MtrApiPatterns:
	def __init__(self, root):
		self.root = root
		data = json.loads((root / 'data/mtr_api/inventory.json').read_text())
		self.alias = {str(l['ID']): l['alias'] for l in data['HR']['metadata']['lines']}

	@lru_cache(maxsize=None)
	def timing(self, line, ids):
		ids = tuple(str(int(x)) for x in ids)
		file = self.root / 'data/mtr_api/raw' / f'HR_{ids[0]}_{ids[-1]}.json'
		if not file.exists():
			raise ValueError(
				f'Missing MTR API journey: {line} {ids[0]}>{ids[-1]}; no distance fallback permitted'
			)
		v = json.loads(file.read_text())
		for route in v.get('response', {}).get('routes', []):
			path = route.get('path') or []
			if tuple(str(int(n['ID'])) for n in path) != ids:
				continue
			if any(
				n.get('linkType') != 'RIDE'
				or self.alias.get(str(n.get('lineID'))) != line
				for n in path[:-1]
			):
				continue
			if path[-1].get('linkType') != 'END':
				continue
			if any(n.get('time') is None for n in path):
				continue
			times = [round(float(n['time']) * 60) for n in path]
			if times[0] != 0 or any(b <= a for a, b in zip(times, times[1:])):
				continue
			if times[-1] != round(float(route['time']) * 60):
				continue
			segments = []
			for i, (a, b) in enumerate(zip(ids, ids[1:])):
				segments.append(
					dict(
						key=f'MTR:{line}:{a}>{b}',
						seconds=times[i + 1] - times[i],
						kind='MTR_API_CUMULATIVE',
						source='MTR official journey planner',
						source_urls=[v['url']],
						supporting_origins=1,
						fetched_at=v['fetched_at'],
						semantics='operator_cumulative_journey_minutes',
						reason='Difference between consecutive cumulative times in one matching MTR API train path. The API journey estimate may include an initial boarding allowance; it is not a distance/speed calculation.',
					)
				)
			return dict(
				seconds=times,
				segments=segments,
				url=v['url'],
				fetched_at=v['fetched_at'],
				api_total_seconds=times[-1],
				path=list(ids),
				messages=route.get('messages') or [],
				semantics='MTR platform-to-platform journey estimate; possible initial waiting is not separately identified by the API.',
			)
		raise ValueError(
			f'No matching MTR API train path for {line} {ids}; no distance fallback permitted'
		)
