"""Run the pinned independent validator locally; fail on any ERROR notices."""

import subprocess, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
	jar = ROOT / 'tools/gtfs-validator-8.0.1-cli.jar'
	out = ROOT / 'data/generated'
	if not jar.exists():
		raise SystemExit(
			'Missing tools/gtfs-validator-8.0.1-cli.jar; see GTFS_BUILD_REVIEW.md'
		)
	with (out / 'validator.log').open('w') as log:
		subprocess.run(
			[
				'java',
				'-Xmx3G',
				'-jar',
				str(jar),
				'-i',
				str(out / 'hk-transit-EXPERIMENTAL.gtfs.zip'),
				'-o',
				str(out / 'validator'),
				'-c',
				'HK',
				'-t',
				'2',
				'-svu',
			],
			stdout=log,
			stderr=subprocess.STDOUT,
			check=True,
		)
	report = json.loads((out / 'validator/report.json').read_text())
	errors = [n for n in report['notices'] if n['severity'] == 'ERROR']
	system = json.loads((out / 'validator/system_errors.json').read_text())
	if errors or system.get('notices'):
		print('GTFS validation FAILED; inspect data/generated/validator/report.json')
		sys.exit(2)
	quality = out / 'quality_report.json'
	if quality.exists():
		data = json.loads(quality.read_text())
		data['independent_validator'] = {
			'version': '8.0.1',
			'error_notice_count': 0,
			'report': 'data/generated/validator/report.json',
		}
		quality.write_text(json.dumps(data, ensure_ascii=False, indent=2))
	print(
		'Independent GTFS validation passed: zero ERROR notices. This does not verify real-world travel times.'
	)


if __name__ == '__main__':
	main()
