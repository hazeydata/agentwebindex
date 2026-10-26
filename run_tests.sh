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
# >>> AC24e confirmation_diff (a FAIL must stop the script; `cmd && echo` would be exempt from set -e)
# AC24e: a vendor_confirmation change must ride in a data/docs-only PR (base: AWI_CONFIRMATION_BASE, default origin/main; missing base fails)
base="${AWI_CONFIRMATION_BASE:-origin/main}"
git rev-parse -q --verify "$base^{commit}" >/dev/null || { echo "FAILED: confirmation_diff base $base not found (git fetch origin main)"; exit 1; }
# R21 (2): AWI_CONFIRMATION_BASE is a local, advisory override only (CI never sets or reads it); an empty diff passes, so say so loudly
if [ "$(git rev-parse "$base^{commit}")" = "$(git rev-parse "HEAD^{commit}")" ]; then echo "WARNING: confirmation_diff base $base is the same commit as HEAD: the diff is empty and this local check proves nothing (AWI_CONFIRMATION_BASE is local and advisory; CI judges the event's base and head SHAs)"; fi
if ! python3 -m awibench.confirmation_diff "$base"; then echo "FAILED: confirmation_diff vs $base"; exit 1; fi
echo "confirmation_diff OK vs $base"
# <<< AC24e
if [ -f ../src/app.js ] && [ -f ../tests/app_smoke.js ]; then nice -n 19 node ../tests/app_smoke.js; fi
left=$(find "$TMPDIR" -mindepth 1 -maxdepth 1 -name 'awi-*' | wc -l)
if [ "$left" -ne 0 ]; then echo "FAILED: test run left $left awi-* temp dirs behind"; exit 1; fi
echo "temp hygiene OK: 0 awi-* dirs left"
