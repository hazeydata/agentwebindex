#!/bin/bash
# AgentWebIndex test suite (stdlib unittest; pytest is not installed on the box). Run via:
#   QRUN_OWNER=implement qrun bash ./run_tests.sh
set -e
cd "$(dirname "$0")/bench"
nice -n 19 python3 -m unittest -v tests.test_bench 2>&1 | grep -E "\.\.\. |^Ran |^OK|^FAILED|^(FAIL|ERROR):"
nice -n 19 python3 tests/dry_run.py | grep -E "news meta|validator|untraced|^MOCK"
nice -n 19 python3 -m awibench.secrets_scan
if [ -f ../src/app.js ] && [ -f ../tests/app_smoke.js ]; then nice -n 19 node ../tests/app_smoke.js; fi
