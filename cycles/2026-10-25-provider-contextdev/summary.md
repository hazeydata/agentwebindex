# AWI-PROVIDER-CONTEXTDEV-001: cycle summary

Date: 2026-10-27 (R37.1 docs follow-up). Author: Fred Hazelton (implemented by Grok Bot).

**Status:** contextdev is open on main. Spec R41.1/R41.2 are built in hazeydata/agentwebindex#45 (this text is the state once #45 merges; #45 already contains #44); R41.3 keeps the `benchmark` workflow disabled until Kitty and a Weatherman SHIP. The flip-only PR hazeydata/agentwebindex#41 (head `a98dc1d6`) merged as merge commit `9dd16aac` on 2026-10-27 at 12:11 AM ET, with base-judged run 40446997862 and advisory run 40446997693, both green at `a98dc1d6` (R33.4). The docs follow-up (R37.1) was hazeydata/agentwebindex#42, docs only; it merged as merge commit `fe369f6c` on 2026-10-27.

State on main:
- bench/config.yaml: `free_tier_guard.contextdev.live_run_allowed` is `true` and `vendor_confirmation` is the record below.
- bench/tos_matrix.yaml: `providers.contextdev.vendor_confirmation_required` stays `true` and `vendor_confirmation` holds the same record.
- Record: date 2026-10-25; sender "@mynameisyahia (Yahia Bakour, Founder and CEO, Context.dev)"; quote "@HazeyDataFred Hello! Can you add https://t.co/TjSwtUVs0K as well?"; evidence_path `cycles/2026-10-25-provider-contextdev/evidence/posts-api.json.txt`; recorded_pr https://github.com/hazeydata/agentwebindex/pull/41.
- `guard.live_allowed` is true for contextdev. It still makes zero calls without `CONTEXT_DEV_API_KEY` (the scheduled workflows set none). It stays unpublished: `publish_hold` lists it and the AC23 gate (P0, P1) fails.
- Keenable is open on main: #36 (R33 2c / R36) squash-merged as `ad262f1c08cfc6909d19f813f3d315a1353db9e0` (2026-10-27 12:52 AM ET), with `live_run_allowed: true` and the record with `recorded_pr` #36. Keenable is keyless and publishable (not in `publish_hold`; AC23 does not apply). The `benchmark` workflow is `disabled_manually` in the repo's Actions settings, so no scheduled run calls it. The `benchmark` workflow is approved for re-enable to run live on schedule within the caps (spec R40), sequenced after paper desk R25 reaches Kitty; state as of this PR: `disabled_manually` in the repo's Actions settings. Stopping any run that would go over a cap, or that adds a new provider, and sending it back to Lucille 2 is spec R40.3, detailed in spec R41 (R41.1 pre-run over-cap projection, R41.2 unknown or uncapped provider stop) and built in hazeydata/agentwebindex#45: `runner.check_run` refuses the whole run before any provider call (0 calls, exit 2, `over_cap_projected.json`; see Pre-run check); the per-call guards stay as the backstop mid-run. Spec R41.3: the workflow stays disabled until R40.4 and R41 pass with Kitty and a Weatherman SHIP. AWI-KEENABLE-HOLD is cancelled: keenable is not added to `publish_hold` and the cap guards are unchanged (Lucille 2, Side Hustle CEO, 1:33 AM ET Oct 27, agent-asserted). Live runs are covered by Fred's Oct 26 go within spend caps (R24, R39); there is no per-run go.

**Next action:** #42 merged (`fe369f6c`), #36 merged after its re-sync (squash `ad262f1c`), and the keenable docs follow-up #43 merged as `716e2ed4` (2026-10-27 1:14 AM ET), so R37.1's "nothing else lands on main until #43 merges" is done. Spec R41 (pre-run over-cap projection and provider stop) is built in hazeydata/agentwebindex#45. Next: Kitty and a Weatherman SHIP on the set, merged in order #44, #45, #46 (R41.3).

**Blockers:** none from Fred's go: live runs are covered by Fred's Oct 26 go within spend caps (R24, R39); there is no per-run go. The `benchmark` workflow re-enable is approved (spec R40) and sequenced after paper desk R25 reaches Kitty (state as of this PR: `disabled_manually`); AWI-KEENABLE-HOLD is cancelled (see decisions.md, "Keenable hold cancelled; benchmark re-enable approved").

**Fixed:** README.md, bench/README.md and SPEC-bench.md describe contextdev as open, with the record summary; tos_matrix `providers.contextdev.note` and config `live_run_ruling` match (#42). The follow-up named in #41's docs as TBD is #42. Spec R41.1 (pre-run over-cap projection: refuse the whole run, exit 2, `over_cap_projected.json`) and R41.2 (an unknown provider, or a live provider with no cap entry, stops the run) are built with tests in hazeydata/agentwebindex#45, which also carries the README fixes Kitty t1 found (5 benchmark crons, `MONTHLY_CAP_USD` hardcoded `"0"`, push to main / manual trigger / test step, 8 adapters).

**Still broken:**
- Spec R41.3: the `benchmark` workflow stays `disabled_manually` until R40.4 and R41 pass with Kitty and a Weatherman SHIP; #45 does not enable it. Uploading the artifact needs a PROTECTED benchmark.yml change (CEO or Fred ruling). Owner: Kitty, Weatherman, then Fred or chief for the re-enable.
- Missing list prices (spec R42.1): Apify search (`apify.search`) and contextdev have no list price in cost_columns, so any run that would call them is refused (exit 2; Apify browser is priced and a browser-only run proceeds) until a separate data PR adds each price with the vendor pricing URL, the date read and a capture with SHA256SUMS (its own SHIP). No invented prices. Owner: implement.
- Artifact upload step (spec R42.2): a failure-only `actions/upload-artifact` step for `over_cap_projected.json` needs a separate PROTECTED `.github/workflows/benchmark.yml` PR. Non-blocking follow-up; #45's job-log print, the file and the failed job are enough. Owner: implement.
- (Resolved) Keenable docs follow-up #43 merged as `716e2ed4` (2026-10-27 1:14 AM ET).
- Wayback backfill for the vendor post (R27(5)). Owner: implement.
- AWI-GATES-RECORDED-PR-001 (R37.2, recorded_pr must equal the PR's own URL in CI) is not built. Until then Kitty and Weatherman check by hand. Owner: implement.
- Branch protection on `main` is not visible to AWI's token: `GET repos/hazeydata/agentwebindex/branches/main/protection` returns HTTP 403 ("Resource not accessible by personal access token") and `GET .../rulesets` returns `[]` (same for agentwebindex-meta; re-checked 2026-10-27 3:05 AM ET). Whether `main` requires reviews or status checks cannot be confirmed from here; "never push to main" is a working rule, not a verified protection. Owner: Fred (repo admin) to confirm or add protection.

**Docs match code:** yes for contextdev after #42. Keenable: yes after #43 (open, keyless, publishable since #36 squash-merged as `ad262f1c`); the keenable terms line in README.md, bench/README.md and SPEC-bench.md said `benchmarking: forbidden` (wrong since the committed status is `permitted_by_ruling`, already on main before #42) and is fixed in #42.

**Locked decisions:** decisions.md (R33-R37, with entries at the end).

**Learnings:** #27 merged as a squash (`8fc84222`). R36.5: R32.2 did not specify a merge method, so this is a note, not a violation.

**Links:** #27, #38 (record source), #40 (gate), #41 (flip), #42 (contextdev docs follow-up), #36 (keenable flip), #43 (keenable docs follow-up), agentwebindex-meta#33.
