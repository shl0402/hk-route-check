#!/usr/bin/env python3
"""Short, numeric rail enrichment findings."""

import json
from normalize import ROOT, OUT


def main():
	r = json.loads((OUT / 'merge_report.json').read_text())
	ps = r['per_line']
	lines = [
		'# Rail GTFS results',
		'',
		f"Active test range: **{r['date_range'][0]}–{r['date_range'][1]}**.",
		'',
		f"**10/10 main MTR lines; 11/11 regular Light Rail routes; 507P + 751P morning specials.**",
		'',
		'| Line | Listed departures | Frequency bands | Baseline fallback bands |',
		'|---|---:|---:|---:|',
	]
	for p in ps:
		lines.append(
			f"| {p['line']} | {p['listed_departures']} | {p['frequency_periods']} | {p['fallback_periods']} |"
		)
	lines += [
		'',
		f"Total: **{r['new_trips']:,} GTFS trip records**, **{r['listed_departures']:,} listed departures**, **{r['frequency_periods']:,} frequency bands**. Records repeat on their service dates; these are not daily train counts.",
		'',
		f"- **{sum(p['conditional_periods'] for p in ps)} conditional model bands**: EAL race/weekday variants and DRL park-open/closed schedules; calendars unverified. These are estimates, not exact departures.",
		f"- **{sum(p['fallback_periods'] for p in ps)} baseline bands** retained where source rows overlap or mix full/short trains.",
		'- **2 circular routes fixed** (705/706); **3 duplicate consecutive stop calls removed**.',
		f"- **{sum(p['platform_calls'] for p in ps):,} Light Rail platform references** attached to trip records. No platform coordinates invented.",
		'- **3 school-day services excluded**: 506P, 610P, 720. School calendars missing.',
		'- **751P main service excluded**: conflicting terminal hours; separately listed morning trips included.',
		'- **3 out-of-scope services excluded**: High Speed Rail and 2 tourist services.',
		'- Source revisions, original rows, first/last times, platform tables and service conditions embedded in GTFS JSON. Evidence-only tables do not change routing.',
		'- Light Rail main timetable dates: **August/September 2025**. Wiki intervals are not independently verified current operator schedules. Rail ride/transfer times remain estimates.',
		'',
	]
	validation = OUT / 'activation_checks.json'
	if validation.exists():
		v = json.loads(validation.read_text())
		if v.get('rail_records') == r['new_trips']:
			lines += [
				f"Validation: **{v['gtfs_errors']} GTFS errors**, **{v['tests_passed']} tests passed**; matching OTP graph active.",
				'',
			]
	(OUT / 'FINDINGS.md').write_text('\n'.join(lines))
	print('\n'.join(lines))


if __name__ == '__main__':
	main()
