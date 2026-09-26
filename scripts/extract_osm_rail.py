"""Extract actual OSM station/entrance features; no geocoding or invented locations."""

import osmium, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Rail(osmium.SimpleHandler):
	def __init__(self):
		super().__init__()
		self.features = []

	def node(self, n):
		tags = dict(n.tags)
		if (
			tags.get('railway') in ('station', 'halt', 'tram_stop', 'subway_entrance')
			or tags.get('public_transport') == 'station'
		):
			self.features.append(
				dict(
					id=f'node/{n.id}', lat=n.location.lat, lon=n.location.lon, tags=tags
				)
			)

	def way(self, w):
		tags = dict(w.tags)
		if (
			tags.get('railway') in ('station', 'halt', 'tram_stop')
			or tags.get('public_transport') == 'station'
		):
			coords = [(n.lat, n.lon) for n in w.nodes if n.location.valid()]
			if coords:
				self.features.append(
					dict(
						id=f'way/{w.id}',
						lat=sum(x[0] for x in coords) / len(coords),
						lon=sum(x[1] for x in coords) / len(coords),
						geometry_method='mean_of_way_vertices',
						tags=tags,
					)
				)


if __name__ == '__main__':
	h = Rail()
	h.apply_file(
		str(ROOT / 'data/raw/2026-09-17/osm/hong-kong-latest.osm.pbf'), locations=True
	)
	out = ROOT / 'data/derived/osm_rail_features.json'
	out.parent.mkdir(parents=True, exist_ok=True)
	out.write_text(json.dumps(h.features, ensure_ascii=False, indent=2))
	print(len(h.features), 'features', out)
