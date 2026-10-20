#!/bin/bash
# AgentWebIndex test suite (pytest; temp files via tmp_path). Run via:
#   QRUN_OWNER=implement qrun bash ./run_tests.sh
set -eo pipefail
cd "$(dirname "$0")/bench"
export TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT
nice -n 19 python3 -m pytest -q -p no:cacheprovider --basetemp="$TMPDIR/pytest" tests/test_bench.py 2>&1 | tail -n 30
nice -n 19 python3 tests/dry_run.py | grep -E "news meta|validator|untraced|^MOCK"
nice -n 19 python3 -m awibench.secrets_scan
if [ -f ../src/app.js ] && [ -f ../tests/app_smoke.js ]; then nice -n 19 node ../tests/app_smoke.js; fi
left=$(find "$TMPDIR" -mindepth 1 -maxdepth 1 -name 'awi-*' | wc -l)
if [ "$left" -ne 0 ]; then echo "FAILED: test run left $left awi-* temp dirs behind"; exit 1; fi
echo "temp hygiene OK: 0 awi-* dirs left"
