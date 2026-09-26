#!/usr/bin/env python3
"""Resumable official MTR all-pairs evidence collection. No fabricated fallback values."""

import argparse, concurrent.futures, fcntl, hashlib, json, os, pathlib, subprocess, time, urllib.parse
from datetime import datetime, timezone


def save(path, value):
	temp = path.with_suffix(path.suffix + '.tmp')
	temp.write_text(json.dumps(value, ensure_ascii=False))
	temp.replace(path)


def main():
	p = argparse.ArgumentParser(description=__doc__)
	p.add_argument('--root', type=pathlib.Path, required=True)
	p.add_argument('--workers', type=int, default=2, choices=[1, 2])
	p.add_argument('--limit', type=int)
	p.add_argument('--offline', action='store_true')
	a = p.parse_args()
	out = a.root / 'data/mtr_api'
	raw = out / 'raw'
	raw.mkdir(parents=True, exist_ok=True)
	lock = (out / 'scraper.lock').open('w')
	try:
		fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
	except BlockingIOError:
		raise SystemExit('Scraper already running; inspect data/mtr_api/progress.json.')
	process = out / 'process.json'
	if process.exists():
		pid = json.loads(process.read_text()).get('pid')
		if pid and pid != os.getpid():
			cmd = subprocess.run(
				['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True
			).stdout
			if 'scripts/scrape_mtr_api.py' in cmd:
				raise SystemExit(
					'Existing scraper is still collecting; no duplicate requests started.'
				)
	save(process, {'pid': os.getpid()})
	audit = a.root / 'data/mtr_api_audit/2026-09-21'
	jobs = []
	inventory = {}
	for system, filename, endpoint in [
		('HR', 'heavyRailDetails.json', 'HRRoutes'),
		('LR', 'lightRailDetails.json', 'LRRoute'),
	]:
		meta = json.loads((audit / filename).read_text())
		ids = sorted(
			{str(int(s['ID'])) for s in meta['stations'] if str(s['ID']) != '888'},
			key=int,
		)
		inventory[system] = {
			'stations': len(ids),
			'ordered_pairs': len(ids) * (len(ids) - 1),
			'metadata': meta,
		}
		for o in ids:
			for d in ids:
				if o != d:
					jobs.append((system, endpoint, o, d))
	save(out / 'inventory.json', inventory)
	# Put adjacent official line pairs first, then cover the entire ordered matrix.
	adjacent = set()
	for system, v in inventory.items():
		for line in v['metadata']['lines']:
			ids = [str(int(x)) for x in line['stationIDs']]
			for o, d in zip(ids, ids[1:]):
				adjacent.update([(system, o, d), (system, d, o)])
	jobs.sort(
		key=lambda j: (
			0 if (j[0], j[2], j[3]) in adjacent else 1,
			j[0],
			int(j[2]),
			int(j[3]),
		)
	)

	def key(j):
		return '_'.join([j[0], j[2], j[3]])

	def good(path):
		try:
			v = json.loads(path.read_text())
			return (
				v.get('status') == 'ok' and str(v['response'].get('errorCode')) == '0'
			)
		except (OSError, ValueError, KeyError):
			return False

	cached = sum(good(raw / (key(j) + '.json')) for j in jobs)
	pending = [j for j in jobs if not good(raw / (key(j) + '.json'))]
	if a.offline:
		allowed = {
			('HR', '39', '44'),
			('HR', '44', '39'),
			('HR', '40', '45'),
			('HR', '45', '40'),
			('HR', '42', '46'),
			('HR', '46', '42'),
		}
		missing = [
			j
			for j in pending
			if (j[0], j[2], j[3]) not in allowed
			or not (raw / (key(j) + '.json')).exists()
		]
		if missing:
			raise SystemExit(
				f'Offline cache incomplete: {len(missing)} MTR pairs. Rerun without --offline to resume.'
			)
		pending = []
	if a.limit:
		pending = pending[: a.limit]
	started = time.time()
	state = {
		'total': len(jobs),
		'cached': cached,
		'completed_this_run': 0,
		'failed_this_run': 0,
		'pending_at_start': len(pending),
		'started_at': datetime.now(timezone.utc).isoformat(),
	}

	def fetch(j):
		system, endpoint, o, d = j
		url = (
			'https://www.mtr.com.hk/share/customer/jp/api/'
			+ endpoint
			+ '/?'
			+ urllib.parse.urlencode({'o': o, 'd': d, 'lang': 'E'})
		)
		result = {
			'system': system,
			'origin': o,
			'destination': d,
			'url': url,
			'fetched_at': datetime.now(timezone.utc).isoformat(),
			'status': 'error',
		}
		for attempt in range(3):
			time.sleep(0.35 if attempt == 0 else 2**attempt)
			proc = subprocess.run(
				['curl', '-fsSL', '--max-time', '35', url],
				capture_output=True,
				text=True,
			)
			try:
				body = json.loads(proc.stdout)
				result['response'] = body
				if proc.returncode or str(body.get('errorCode')) != '0':
					raise ValueError(str(body.get('errorMsg', proc.stderr)))
				if not isinstance(body.get('routes'), list):
					raise ValueError('Missing route list')
				result.update(
					status='ok',
					response=body,
					sha256=hashlib.sha256(proc.stdout.encode()).hexdigest(),
				)
				break
			except (ValueError, AttributeError) as exc:
				result['error'] = str(exc)[:500] or proc.stderr[:500]
		save(raw / (key(j) + '.json'), result)
		return result['status']

	with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
		# Bounded queue: no thousands of futures held in memory.
		it = iter(pending)
		active = {}
		for j in [next(it, None) for _ in range(a.workers)]:
			if j:
				active[pool.submit(fetch, j)] = j
		while active:
			done, _ = concurrent.futures.wait(
				active, return_when=concurrent.futures.FIRST_COMPLETED
			)
			for future in done:
				active.pop(future)
				status = future.result()
				state[
					'completed_this_run' if status == 'ok' else 'failed_this_run'
				] += 1
				elapsed = time.time() - started
				attempted = state['completed_this_run'] + state['failed_this_run']
				state.update(
					elapsed_seconds=round(elapsed),
					remaining=len(pending) - attempted,
					estimated_remaining_seconds=round(
						(len(pending) - attempted) * elapsed / attempted
					),
					updated_at=datetime.now(timezone.utc).isoformat(),
				)
				save(out / 'progress.json', state)
				if attempted % 20 == 0 or attempted == len(pending):
					print(json.dumps(state), flush=True)
				j = next(it, None)
				if j:
					active[pool.submit(fetch, j)] = j
	state['finished'] = True
	save(out / 'progress.json', state)


if __name__ == '__main__':
	main()
