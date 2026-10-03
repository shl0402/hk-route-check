#!/usr/bin/env python3
"""Run portable checks without a transport-data download or a running server."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = ('test_transit_enrichment*.py', 'test_official_transit_sources.py', 'test_historical_transit_sources.py', 'test_transit_arrivals.py', 'test_transit_published.py', 'test_app_routing_sync.py', 'test_operator_timing.py', 'test_route_selection.py', 'test_transit_identity.py', 'test_surface_timing.py', 'test_setup.py', 'test_landsd.py', 'test_indoor_network.py', 'test_indoor_release.py', 'test_place_search.py',
            'test_optimizer.py', 'test_fast_mode.py', 'test_large_mode.py',
            'test_travel_cache_option.py', 'test_mtr_branch.py', 'test_route_alternatives.py')

if __name__ == '__main__':
    suite = unittest.TestSuite()
    suite.addTests(unittest.TestLoader().discover(str(ROOT / 'tests'), pattern='test_n796_departures.py'))
    for pattern in PATTERNS:
        suite.addTests(unittest.TestLoader().discover(str(ROOT / 'tests'), pattern=pattern))
    suite.addTests(unittest.TestLoader().discover(str(ROOT / 'scripts/hkbus_pilot'), pattern='test_scraper.py'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
