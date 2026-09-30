"""Provider identity from the active GTFS, independent of OTP transport modes."""
import csv
import io
import zipfile
from functools import lru_cache

# App palette, not an assertion about the colour of a particular vehicle.
# map line, badge background, badge text (opaque colours keep text readable).
PALETTES = {
	'KMB': ('C43D44', 'FBE7E8', '8B2028'),
	'CTB': ('D1A125', 'FFF0BC', '685000'),
	'GMB': ('8FAF83', 'E5EFDF', '38552F'),
	'LWB': ('C77832', 'FBEBD9', '75400C'),
	'joint': ('B77B38', 'F5E8D7', '704819'),
	'other': ('627687', 'E8EEF2', '374B5B'),
}
LABELS = {'KMB': 'KMB', 'CTB': 'Citybus', 'LWB': 'Long Win', 'GMB': 'Green minibus',
	'NLB': 'New Lantao Bus', 'LRTFeeder': 'MTR Bus'}


@lru_cache(maxsize=4)
def _routes(path, modified, size):
	with zipfile.ZipFile(path) as archive:
		def rows(name):
			return csv.DictReader(io.StringIO(archive.read(name).decode('utf-8-sig')))
		agencies = {row['agency_id']: row['agency_name'] for row in rows('agency.txt')}
		result = {}
		for row in rows('routes.txt'):
			agency = row['agency_id']
			operators = agency.split('+')
			kind = 'minibus' if agency == 'GMB' else 'bus' if row['route_type'] == '3' else None
			if kind is None:
				continue
			palette = 'joint' if len(operators) > 1 else agency if agency in PALETTES else 'other'
			line, background, text = PALETTES[palette]
			result[row['route_id']] = {
				'agencyId': agency, 'operatorIds': operators,
				'operatorName': ' / '.join(LABELS.get(op, agencies.get(op, op)) for op in operators),
				'serviceKind': kind, 'displayColor': line,
				'badgeColor': background, 'textColor': text,
			}
		return result


def enrich_leg(leg, root):
	"""Add optional route fields; keep BUS mode so existing route filters still work."""
	if leg.get('mode') not in ('BUS', 'COACH', 'MINIBUS'):
		return
	route = leg.get('route')
	if not route:
		return
	feed = root / 'data/generated/hk-transit-EXPERIMENTAL.gtfs.zip'
	stat = feed.stat()
	identities = _routes(str(feed), stat.st_mtime_ns, stat.st_size)
	identifier = route.get('gtfsId') or ''
	identity = identities.get(identifier) or identities.get(identifier.partition(':')[2])
	if identity:
		route.update(identity)
		leg['transportGroup'] = identity['serviceKind']
		leg['transportLabel'] = identity['operatorName']
