#!/usr/bin/env python3
"""Resumable article collection; reuses pilot sources, never modifies GTFS."""

import argparse, collections, fcntl, hashlib, json, sqlite3, sys, time
from datetime import datetime, timezone
from pathlib import Path
from scraper import Client, ROOT, compare, extract, save

VERSION = 'bulk-extractor-1'


def now():
	return datetime.now(timezone.utc).isoformat()


def raw_path(out, title):
	return out / 'raw' / (hashlib.sha256(title.encode()).hexdigest()[:20] + '.json')


def choose(item, entries):
	# A missing numerical match alone is NOT evidence of irrelevance.
	if item['gtfs_candidates']:
		return True, 'operator/number candidate; identity unresolved'
	if entries and all(e.get('status_hint') == 'inactive' for e in entries):
		return False, 'only listed under explicitly inactive sections'
	title = item['wiki_title']
	if title.startswith('居民巴士') or '僱員服務' in title or '員工' in title:
		return (
			False,
			'resident/employee service with no GTFS candidate; excluded from public worker transport collection',
		)
	if entries and all(e['directory'] == '免費穿梭巴士路線列表' for e in entries):
		return (
			False,
			'non-GTFS venue shuttle; excluded from this public bus/minibus collection',
		)
	return True, 'public transport or uncertain relevance; keep for later matching'


def connect(path):
	db = sqlite3.connect(path)
	db.execute('PRAGMA journal_mode=WAL')
	db.execute('''CREATE TABLE IF NOT EXISTS jobs (
      title TEXT PRIMARY KEY, selected INTEGER, reason TEXT, state TEXT DEFAULT 'pending',
      attempts INTEGER DEFAULT 0, pageid INTEGER, revision TEXT, updated TEXT,
      error TEXT, duration REAL, version TEXT, article_path TEXT)''')
	return db


def status(db):
	counts = dict(
		db.execute('SELECT state,count(*) FROM jobs WHERE selected=1 GROUP BY state')
	)
	total = sum(counts.values())
	done = counts.get('done', 0)
	durations = [
		r[0]
		for r in db.execute(
			"SELECT duration FROM jobs WHERE selected=1 AND duration>0 AND state='done' ORDER BY updated DESC LIMIT 50"
		)
	]
	avg = sum(durations) / len(durations) if durations else None
	remaining = total - done
	return dict(
		updated_at=now(),
		total_selected=total,
		done=done,
		remaining=remaining,
		states=counts,
		excluded=db.execute('SELECT count(*) FROM jobs WHERE selected=0').fetchone()[0],
		average_seconds=round(avg, 2) if avg else None,
		estimated_remaining_minutes=round(remaining * avg / 60, 1) if avg else None,
	)


def report(db, out):
	s = status(db)
	save(out / 'bulk_status.json', s)
	rows = db.execute(
		'SELECT title,reason,state,error,pageid FROM jobs WHERE selected=1 AND state!=\'done\''
	).fetchall()
	save(
		out / 'bulk_unfinished.json',
		[dict(zip(['title', 'reason', 'state', 'error', 'pageid'], r)) for r in rows],
	)
	return s


def run(args):
	out = Path(args.output)
	out.mkdir(parents=True, exist_ok=True)
	if args.status:
		if not (out / 'bulk.sqlite').exists():
			raise SystemExit('No queue yet. Run --prepare first.')
		db = sqlite3.connect('file:' + str(out / 'bulk.sqlite') + '?mode=ro', uri=True)
		print(json.dumps(status(db), ensure_ascii=False, indent=2))
		db.close()
		return 0
	lock = (out / 'bulk.lock').open('a')
	try:
		fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
	except BlockingIOError:
		raise SystemExit('Another bulk collector is running. Use --status.')
	db = connect(out / 'bulk.sqlite')
	inventory = json.loads((out / 'inventory.json').read_text())
	comparison = compare(inventory['entries'], args.gtfs)
	save(out / 'bulk_comparison.json', comparison)
	grouped = collections.defaultdict(list)
	for e in inventory['entries']:
		grouped[e['title']].append(e)
	decisions = []
	for item in comparison['comparisons']:
		title = item['wiki_title']
		selected, reason = choose(item, grouped[title])
		db.execute(
			'INSERT INTO jobs(title,selected,reason) VALUES(?,?,?) ON CONFLICT(title) DO UPDATE SET selected=excluded.selected,reason=excluded.reason',
			(title, int(selected), reason),
		)
		decisions.append(
			dict(
				title=title,
				selected=selected,
				reason=reason,
				gtfs_candidates=item['gtfs_candidates'],
			)
		)
	save(out / 'bulk_selection.json', decisions)
	# Any interrupted request resumes. A completed row is skipped only if artifacts still exist.
	db.execute("UPDATE jobs SET state='pending' WHERE state='running'")
	for title, path, version in db.execute(
		"SELECT title,article_path,version FROM jobs WHERE state='done'"
	).fetchall():
		if (
			version != VERSION
			or not path
			or not (out / 'articles' / Path(path).name).exists()
			or not raw_path(out, title).exists()
		):
			db.execute("UPDATE jobs SET state='pending' WHERE title=?", (title,))
	if args.retry_failed:
		db.execute("UPDATE jobs SET state='pending' WHERE state='failed'")
	if args.refresh:
		db.execute("UPDATE jobs SET state='pending' WHERE selected=1")
	db.commit()
	initial = report(db, out)
	print(json.dumps(initial, ensure_ascii=False), flush=True)
	if args.prepare:
		db.close()
		lock.close()
		return 0
	client = Client(out, offline=args.offline, refresh=args.refresh, delay=args.delay)
	jobs = [
		r[0]
		for r in db.execute(
			"SELECT title FROM jobs WHERE selected=1 AND state='pending' ORDER BY title"
		)
	]
	if args.limit:
		jobs = jobs[: args.limit]
	consecutive_errors = 0
	try:
		for title in jobs:
			start = time.monotonic()
			cached = raw_path(out, title).exists() and not args.refresh
			db.execute(
				"UPDATE jobs SET state='running',attempts=attempts+1,updated=? WHERE title=?",
				(now(), title),
			)
			db.commit()
			print('FETCH ' + title + (' [cache]' if cached else ''), flush=True)
			try:
				record = client.page(title)
				result = extract(record)
				path = out / 'articles' / f"{result['pageid']}.json"
				save(path, result)
				db.execute(
					"UPDATE jobs SET state='done',pageid=?,revision=?,updated=?,error=NULL,duration=?,version=?,article_path=? WHERE title=?",
					(
						result['pageid'],
						str(result.get('revision_id')),
						now(),
						None if cached else time.monotonic() - start,
						VERSION,
						str(path),
						title,
					),
				)
				consecutive_errors = 0
			except Exception as e:
				db.execute(
					"UPDATE jobs SET state='failed',updated=?,error=? WHERE title=?",
					(now(), str(e), title),
				)
				consecutive_errors += 1
				print('ERROR ' + title + ': ' + str(e), flush=True)
			db.commit()
			s = report(db, out)
			print(
				f"PROGRESS {s['done']}/{s['total_selected']} ({100*s['done']/max(1,s['total_selected']):.1f}%) failed={s['states'].get('failed',0)} ETA={s['estimated_remaining_minutes']} min",
				flush=True,
			)
			if consecutive_errors >= 5:
				print(
					'Paused after five consecutive failures; resolve access issue then rerun --retry-failed.',
					flush=True,
				)
				break
	except KeyboardInterrupt:
		db.execute("UPDATE jobs SET state='pending' WHERE state='running'")
		db.commit()
		print('Stopped safely. Rerun the same command to resume.', flush=True)
	finally:
		s = report(db, out)
		print(json.dumps(s, ensure_ascii=False, indent=2), flush=True)
		db.close()
		lock.close()
	return 0 if s['remaining'] == 0 else 2


if __name__ == '__main__':
	ap = argparse.ArgumentParser(description=__doc__)
	ap.add_argument('--output', default=str(ROOT / 'data/wiki_pilot'))
	ap.add_argument('--gtfs', default=str(ROOT / 'data/raw/2026-09-17/td/gtfs.zip'))
	ap.add_argument(
		'--prepare', action='store_true', help='Build selection/queue without fetching'
	)
	ap.add_argument(
		'--status', action='store_true', help='Read progress without fetching'
	)
	ap.add_argument('--retry-failed', action='store_true')
	ap.add_argument('--offline', action='store_true')
	ap.add_argument('--refresh', action='store_true')
	ap.add_argument(
		'--limit',
		type=int,
		default=0,
		help='Maximum pending articles this run; zero means all',
	)
	ap.add_argument('--delay', type=float, default=1.0)
	args = ap.parse_args()
	if args.delay < 0.5 or args.limit < 0:
		ap.error('delay must be >=0.5, limit >=0')
	raise SystemExit(run(args))
