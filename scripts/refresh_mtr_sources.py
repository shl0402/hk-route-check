"""Fetch public MTR service hours; cache raw responses. Never cache guessed values."""

import csv, json, subprocess, time, hashlib
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'data/derived/mtr_service_hours'


def refresh(force=False):
	DEST.mkdir(parents=True, exist_ok=True)
	rows = list(
		csv.DictReader(
			(ROOT / 'data/user_inputs/mtr/mtr_lines_and_stations.csv').open(
				encoding='utf-8-sig'
			)
		)
	)
	origins = sorted(
		{r['Station ID'] for r in rows if r['Sequence'] and float(r['Sequence']) == 1}
	)
	for sid in origins:
		target = DEST / f'{sid}.html'
		if target.exists() and not force:
			continue
		url = f'https://www.mtr.com.hk/en/customer/services/service_hours_search.php?query_type=search&station={sid}'
		temp = target.with_suffix('.part')
		subprocess.run(
			[
				'curl',
				'--fail',
				'--silent',
				'--show-error',
				'--location',
				'--max-time',
				'30',
				'--output',
				str(temp),
				url,
			],
			check=True,
		)
		data = temp.read_bytes()
		if b'firstTrain' not in data:
			raise ValueError(f'No timetable in {url}; not accepting response')
		temp.replace(target)
		target.with_suffix('.source.json').write_text(
			json.dumps(
				dict(
					url=url,
					retrieved_at=datetime.now(timezone.utc).isoformat(),
					sha256=hashlib.sha256(data).hexdigest(),
				),
				indent=2,
			)
		)
		print('Cached service hours:', sid, flush=True)
		time.sleep(1)


if __name__ == '__main__':
	import argparse

	p = argparse.ArgumentParser()
	p.add_argument('--force', action='store_true')
	a = p.parse_args()
	refresh(a.force)
