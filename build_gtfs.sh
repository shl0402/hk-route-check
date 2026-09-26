#!/usr/bin/env sh
# Compatibility entry point: one complete build, including all enrichments and OTP.
set -eu
cd "$(dirname "$0")"
exec python3 run.py build "$@"
