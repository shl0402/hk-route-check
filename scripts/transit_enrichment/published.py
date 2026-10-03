#!/usr/bin/env python3
"""Verified TD route references; never silently replace GTFS timing anchors.

The TD dictionary labels JOURNEY_TIME as an integer "Journey Time" but does not
declare a unit. Preserve that limitation instead of guessing minutes. The raw
value is useful for comparison with separately verified operator/GTFS timings.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET

SOURCES = {
    'ROUTE_BUS.xml': 'https://static.data.gov.hk/td/routes-fares-xml/ROUTE_BUS.xml',
    'td-route-dictionary.pdf': 'https://static.data.gov.hk/td/routes-fares-xml/dataspec/ptroutefare_xml_dataspec.pdf',
    'citybus-n796-timetable.pdf': 'https://www.citybus.com.hk/en/uploadedFiles/cust_notice/TS-NWFB-N796-N796-N.pdf',
    'citybus-n796-extension.pdf': 'https://www.citybus.com.hk/tc/uploadedPressRelease/19104_18022022_795P-795X_chi.pdf',
}
DATASET_URL = ('https://data.gov.hk/en-data/dataset/hk-td-tis_14-routes-fares-xml/'
               'resource/f55ff629-70fd-4406-aa4e-5e0e0574f50b')
MAX_FILE_BYTES = 12_000_000
UNIT_NOTE = ('The TD route dictionary describes JOURNEY_TIME as integer Journey '
             'Time without declaring a unit. No conversion to minutes or '
             'replacement of GTFS timing is performed.')
POLICY = ('Reference metadata, not measured travel times. Route-level values '
          'have no direction, departure-hour or section breakdown. Preserve '
          'existing timing anchors unless independently corroborated.')


def raw_directory(root):
    return Path(root) / 'data/transit_enrichment/raw/published'


def sha(content):
    return hashlib.sha256(content).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.part')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def parse_routes(content):
    """Normalize known identity fields, preserving all raw fields as evidence."""
    if not isinstance(content, bytes) or not content or len(content) > MAX_FILE_BYTES:
        raise ValueError('Expected bounded, nonempty TD XML bytes')
    if b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper() or b'\x00' in content:
        raise ValueError('DTD/entity declarations and non-UTF-8 XML are unsupported')
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError('Malformed TD route XML') from error
    if root.tag != 'dataroot' or not len(root) or len(root) > 100_000:
        raise ValueError('Unexpected TD route XML root or record count')
    routes, missing, invalid = {}, 0, 0
    for row in root:
        if row.tag != 'ROUTE' or not len(row):
            raise ValueError('Unexpected TD route record')
        fields = {}
        for child in row:
            if len(child) or child.tag in fields or not re.fullmatch(r'[A-Z_]+', child.tag):
                raise ValueError('Unexpected or duplicate TD field')
            fields[child.tag] = (child.text or '').strip()
        route_id = fields.get('ROUTE_ID', '')
        if not re.fullmatch(r'[1-9]\d{0,9}', route_id) or route_id in routes:
            raise ValueError('Invalid or duplicate TD route ID')
        if fields.get('ROUTE_TYPE') != '1' or not fields.get('COMPANY_CODE') or not fields.get('ROUTE_NAMEE'):
            raise ValueError('Missing bus route identity or unexpected route type')
        value = fields.get('JOURNEY_TIME', '')
        # Blank/invalid timings must never become zero-duration journeys.
        if not value:
            timing, status = None, 'missing'
            missing += 1
        elif not re.fullmatch(r'[1-9]\d{0,5}', value):
            timing, status = None, 'invalid_positive_integer'
            invalid += 1
        else:
            timing, status = int(value), 'unit_not_declared'
        routes[route_id] = dict(
            route_id=route_id, company_code=fields['COMPANY_CODE'],
            route_names={lang: fields.get('ROUTE_NAME' + suffix, '') for lang, suffix in [('en', 'E'), ('tc', 'C'), ('sc', 'S')]},
            origins={lang: fields.get('LOC_START_NAME' + suffix, '') for lang, suffix in [('en', 'E'), ('tc', 'C'), ('sc', 'S')]},
            destinations={lang: fields.get('LOC_END_NAME' + suffix, '') for lang, suffix in [('en', 'E'), ('tc', 'C'), ('sc', 'S')]},
            service_mode=fields.get('SERVICE_MODE'), special_type=fields.get('SPECIAL_TYPE'),
            journey_time_value=timing, journey_time_raw=value,
            journey_time_unit=None, journey_minutes=None, journey_time_status=status,
            timing_scope='route-level; direction and departure period unspecified',
            can_replace_gtfs_timing=False, last_update_date=fields.get('LAST_UPDATE_DATE'),
            operator_urls={lang: fields.get('HYPERLINK_' + suffix, '') for lang, suffix in [('en', 'E'), ('tc', 'C'), ('sc', 'S')]},
            full_fare_raw=fields.get('FULL_FARE'), raw_fields=fields,
        )
    return routes, dict(routes=len(routes), positive_journey_values=len(routes)-missing-invalid,
                        missing_journey_values=missing, invalid_journey_values=invalid,
                        source_declared_minute_values=0)


def checked_file(directory, name, metadata):
    if name not in SOURCES or metadata.get('url') != SOURCES[name]:
        raise ValueError('Unexpected published-source URL or filename')
    content = (directory / name).read_bytes()
    if sha(content) != metadata.get('sha256') or len(content) != metadata.get('bytes'):
        raise ValueError('Published source cache checksum mismatch: ' + name)
    validate_file(name, content)
    return content


def validate_file(name, content):
    if not content or len(content) > MAX_FILE_BYTES:
        raise ValueError('Empty or oversized published source: ' + name)
    if name.endswith('.xml'):
        parse_routes(content)
    elif not content.startswith(b'%PDF-'):
        raise ValueError('Expected PDF source: ' + name)


def fetch(root, offline=False, refresh=False):
    """Cache approved public artifacts with hashes; reruns resume per artifact.

    Ordinary and offline reruns fail on corruption. Only explicit refresh
    replaces a saved snapshot. Network failure preserves earlier valid files.
    """
    if offline and refresh:
        raise ValueError('Cannot refresh published sources offline')
    directory = raw_directory(root)
    directory.mkdir(parents=True, exist_ok=True)
    mp = directory / 'manifest.json'
    manifest = json.loads(mp.read_text()) if mp.exists() else dict(format=1, files={})
    if manifest.get('format') != 1 or not isinstance(manifest.get('files'), dict):
        raise ValueError('Unsupported published-source manifest')
    manifest.update(dataset_url=DATASET_URL, policy=POLICY, journey_time_unit_note=UNIT_NOTE)
    for name, url in SOURCES.items():
        meta = manifest['files'].get(name)
        if meta and (directory / name).exists() and not refresh:
            checked_file(directory, name, meta)
            continue
        if offline:
            raise ValueError('Missing published source cache: ' + name)
        if (directory / name).exists() and not meta and not refresh:
            raise ValueError('Unverified published source; use explicit refresh: ' + name)
        import requests
        for attempt in range(3):
            try:
                with requests.get(url, timeout=(15, 45), stream=True,
                                  headers={'User-Agent': 'map-routing-source-verification/1'}) as response:
                    response.raise_for_status()
                    if response.url != url:
                        raise ValueError('Unexpected published-source redirect')
                    parts, size = [], 0
                    for part in response.iter_content(65536):
                        size += len(part)
                        if size > MAX_FILE_BYTES:
                            raise ValueError('Published artifact exceeds size bound')
                        parts.append(part)
                    content = b''.join(parts)
                    last_modified = response.headers.get('Last-Modified')
                    etag = response.headers.get('ETag')
                    content_type = response.headers.get('Content-Type')
                validate_file(name, content)
                break
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
        temp = (directory / name).with_suffix(Path(name).suffix + '.part')
        temp.write_bytes(content)
        temp.replace(directory / name)
        manifest['files'][name] = dict(url=url, sha256=sha(content), bytes=len(content),
            retrieved_at=datetime.now(timezone.utc).isoformat(), last_modified=last_modified,
            etag=etag, content_type=content_type)
        manifest['complete'] = all(key in manifest['files'] for key in SOURCES)
        save(mp, manifest)
    manifest['complete'] = True
    save(mp, manifest)
    return manifest


def normalize(root):
    """Replay only verified cached bytes, without network or GTFS mutations."""
    manifest = fetch(root, offline=True)
    directory = raw_directory(root)
    routes, quality = parse_routes(checked_file(directory, 'ROUTE_BUS.xml', manifest['files']['ROUTE_BUS.xml']))
    return dict(routes=routes, quality=quality, source=dict(
        name='Transport Department route reference', url=SOURCES['ROUTE_BUS.xml'],
        dataset_url=DATASET_URL, dictionary_url=SOURCES['td-route-dictionary.pdf'],
        **{key: manifest['files']['ROUTE_BUS.xml'][key] for key in ['sha256', 'retrieved_at', 'last_modified']},
        journey_time_unit=None, journey_time_unit_note=UNIT_NOTE, policy=POLICY,
        files=manifest['files'],
    ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['fetch', 'check'], nargs='?', default='fetch')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    if args.action == 'fetch':
        fetch(args.root, offline=args.offline, refresh=args.refresh)
    print(json.dumps(normalize(args.root)['quality'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
