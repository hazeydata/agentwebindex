# AWI-PROVIDER-CONTEXTDEV-001: decisions

## R30: live_allowed test made fixture-strict for contextdev (2026-10-26)
- Problem: `K1Committed.test_ac3_live_allowed_generalized` asserted `live_allowed(CFG, TOS, "contextdev")` (and the default id) is False on the committed config. #28 commits the contextdev confirmation, which makes it True, so the test failed on #28.
- Ruling (R30): check contextdev on fixtures, strict both ways: no confirmation record gives False; a valid record gives True. No skip or xfail. Keenable assertions unchanged. Test files and this entry only; no PROTECTED file (`confirmation_diff.PROTECTED`, e.g. gates.py) and no config (config.yaml, tos_matrix.yaml) changed.
- Order: this test-only PR gets its own SHIP and merge into feat/awi-provider-contextdev-001 first. Then #28 re-syncs on the new base, with `python3 -m awibench.confirmation_diff origin/feat/awi-provider-contextdev-001 HEAD` rc=0 and the full suite green, before #28 proceeds.
