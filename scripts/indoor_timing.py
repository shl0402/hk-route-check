"""One lift-wait owner for the indoor model, GTFS and OTP 2.9.

OTP adds elevator.boardSlack independently of a GTFS lift's traversal_time.
The feed therefore contains movement only; OTP charges the modelled wait once.
"""
import argparse
import json
from pathlib import Path

LIFT_WAIT_SECONDS = 20


def pathway_seconds(edge):
	seconds = edge['seconds']
	if edge['mode'] == 5:
		seconds -= LIFT_WAIT_SECONDS
	return max(1, seconds)


def write_otp_config(graph):
	graph = Path(graph)
	# Keep build-time transfers and runtime access/egress on the same model.
	for filename, section in (
		('build-config.json', 'transferRequests'),
		('router-config.json', 'routingDefaults'),
	):
		path = graph / filename
		config = json.loads(path.read_text()) if path.exists() else {}
		if section == 'transferRequests':
			requests = config.setdefault(section, [{'modes': 'WALK'}])
			if not isinstance(requests, list):
				raise ValueError('OTP transferRequests must be an array of requests')
		else:
			requests = [config.setdefault(section, {})]
		for request in requests:
			request.setdefault('elevator', {})['boardSlack'] = f'PT{LIFT_WAIT_SECONDS}S'
		path.write_text(json.dumps(config, indent=2) + '\n')


if __name__ == '__main__':
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument('graph', type=Path)
	write_otp_config(parser.parse_args().graph)
