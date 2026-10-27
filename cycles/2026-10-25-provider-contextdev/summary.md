# AWI-PROVIDER-CONTEXTDEV-001: cycle summary

Date: 2026-10-27 (R37.1 docs follow-up). Author: Fred Hazelton (implemented by Grok Bot).

**Status:** contextdev is open on main. The flip-only PR hazeydata/agentwebindex#41 (head `a98dc1d6`) merged as merge commit `9dd16aac` on 2026-10-27 at 12:11 AM ET, with base-judged run 40446997862 and advisory run 40446997693, both green at `a98dc1d6` (R33.4). The docs follow-up (R37.1) was hazeydata/agentwebindex#42, docs only; it merged as merge commit `fe369f6c` on 2026-10-27.

State on main:
- bench/config.yaml: `free_tier_guard.contextdev.live_run_allowed` is `true` and `vendor_confirmation` is the record below.
- bench/tos_matrix.yaml: `providers.contextdev.vendor_confirmation_required` stays `true` and `vendor_confirmation` holds the same record.
- Record: date 2026-10-25; sender "@mynameisyahia (Yahia Bakour, Founder and CEO, Context.dev)"; quote "@HazeyDataFred Hello! Can you add https://t.co/TjSwtUVs0K as well?"; evidence_path `cycles/2026-10-25-provider-contextdev/evidence/posts-api.json.txt`; recorded_pr https://github.com/hazeydata/agentwebindex/pull/41.
- `guard.live_allowed` is true for contextdev. It still makes zero calls without `CONTEXT_DEV_API_KEY` (the scheduled workflows set none). It stays unpublished: `publish_hold` lists it and the AC23 gate (P0, P1) fails.
- Keenable is open on main: #36 (R33 2c / R36) squash-merged as `ad262f1c08cfc6909d19f813f3d315a1353db9e0` (2026-10-27 12:52 AM ET), with `live_run_allowed: true` and the record with `recorded_pr` #36. Keenable is keyless and publishable (not in `publish_hold`; AC23 does not apply). The `benchmark` workflow is `disabled_manually` in the repo's Actions settings, so no scheduled run calls it; Lucille 2 (AWI owner) ruled it stays disabled until a per-run go (agent-asserted). Each live run needs Fred's go per run (agent-asserted; spec R24/R25 record an earlier go relayed via the CEO).

**Next action:** #42 merged (`fe369f6c`), and #36 merged after its re-sync (squash `ad262f1c`). Next: Kitty, then a Weatherman SHIP on the keenable docs follow-up #43 at its exact head. Nothing else lands on main until #43 merges (R37.1).

**Blockers:** Each live run needs Fred's go per run (agent-asserted; spec R24/R25 record an earlier go relayed via the CEO).

**Fixed:** README.md, bench/README.md and SPEC-bench.md describe contextdev as open, with the record summary; tos_matrix `providers.contextdev.note` and config `live_run_ruling` match (#42). The follow-up named in #41's docs as TBD is #42.

**Still broken:**
- Keenable docs follow-up: hazeydata/agentwebindex#43 (owner implement; R37.1, Keenable R10), open; nothing else lands on main until it merges.
- Wayback backfill for the vendor post (R27(5)). Owner: implement.
- AWI-GATES-RECORDED-PR-001 (R37.2, recorded_pr must equal the PR's own URL in CI) is not built. Until then Kitty and Weatherman check by hand. Owner: implement.

**Docs match code:** yes for contextdev after #42. Keenable: yes after #43 (open, keyless, publishable since #36 squash-merged as `ad262f1c`); the keenable terms line in README.md, bench/README.md and SPEC-bench.md said `benchmarking: forbidden` (wrong since the committed status is `permitted_by_ruling`, already on main before #42) and is fixed in #42.

**Locked decisions:** decisions.md (R33-R37, with entries at the end).

**Learnings:** #27 merged as a squash (`8fc84222`). R36.5: R32.2 did not specify a merge method, so this is a note, not a violation.

**Links:** #27, #38 (record source), #40 (gate), #41 (flip), #42 (contextdev docs follow-up), #36 (keenable flip), #43 (keenable docs follow-up), agentwebindex-meta#33.
