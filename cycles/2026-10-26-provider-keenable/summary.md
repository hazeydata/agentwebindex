# AWI-PROVIDER-KEENABLE-001 K2 flip (2026-10-26)

Flip-only PR (AC24e, #36), squash-merged into main as `ad262f1c` on 2026-10-27; history below. Re-lands the K2 flip. **R36 (2026-10-26 11:59 PM ET, Spec via chief):** #36 is retargeted from `feat/awi-provider-contextdev-001` (merged into main by #27, squash 8fc84222) to `main`, and main was merged into its branch with a normal merge commit (no rebase, no force-push). This is R33 (2c), the keenable flip into main. Changes vs main:
- `bench/config.yaml` `free_tier_guard.keenable.live_run_allowed`: false -> true; `vendor_confirmation`: null -> the record below. `paid_usage_allowed` stays false.
- `bench/tos_matrix.yaml` `providers.keenable.vendor_confirmation`: null -> the same record.
- Record: date "2026-10-25", sender "@styskin (Andrey Styskin, CEO and co-founder, Keenable)", quote "Please include @KeenableAI into your tests.", evidence_path `cycles/2026-10-26-provider-keenable/evidence/posts-api.json.txt` (the saved public X API read; archive.org 523 and archive.today 429 on the Oct 26 08:45 EDT retry; HTML capture in `evidence/post-capture-og.html.txt`, SHA256SUMS), recorded_pr https://github.com/hazeydata/agentwebindex/pull/36 (this PR; per Spec Keenable R8, changed from #30).
- Evidence: `evidence/posts-api.json.txt`. Decisions and the fact-check citation: decisions.md.

No live keenable calls have been made. #36 merged on its own Weatherman SHIP with confirmation-diff-base green at its head (AC11). Each live run needs Fred's go per run (agent-asserted; spec R24/R25 record an earlier go relayed via the CEO).

**Still broken:**
- Resolved by hazeydata/agentwebindex#43 (the R37.1 keenable docs follow-up, owner implement; split forced by R13): README.md, bench/README.md and SPEC-bench.md, tos_matrix `providers.keenable.note` and config `free_tier_guard.keenable.live_run_ruling` now describe keenable as open. Until #43 merges nothing else lands on main (R37.1).
- The 4 uuid-bearing lines in `bench/runs/2026-10-26/apify/news_2026-44.jsonl` are on main; they came in with #31 (`286991a4`). #36 was squash-merged so its branch commit `343ac530` did not add them again. Removing them from main needs a history rewrite. That is Fred's call (data owner, flagged by QG). Owner: Fred.
- Keenable is keyless, so with its gate open any harness run calls it. The `benchmark` workflow is disabled in the repo's Actions settings today, so no scheduled run calls keenable. Re-enabling it would call keenable on every scheduled run. Lucille 2 (AWI owner) ruled it stays disabled until a per-run go (agent-asserted). Each live run needs Fred's go per run (agent-asserted; spec R24/R25 record an earlier go relayed via the CEO). Owner: Fred (whether and when to re-enable).

**Docs match code:** yes after #43 (checked on the committed files: `guard.live_allowed` true for contextdev and keenable; `gates.publishable` false for contextdev, true for keenable; `publish_hold` is `["contextdev"]`).

**Update (2026-10-27, after #36 merged):** #36 squash-merged into main as `ad262f1c08cfc6909d19f813f3d315a1353db9e0` (12:52 AM ET). The keenable docs follow-up is hazeydata/agentwebindex#43, named in the update below.

**Update (2026-10-27, R37 re-sync):** #41 (contextdev flip) merged into main as merge commit `9dd16aac`, and #42 (contextdev docs follow-up, R37.1) merged as merge commit `fe369f6c`. Main `fe369f6c` was merged into this branch with a normal merge commit (no rebase, no force-push). The diff vs main is still only the 8 keenable files. Keenable docs follow-up: hazeydata/agentwebindex#43 (owner implement); split forced by R13. It updates README.md, bench/README.md, SPEC-bench.md, tos_matrix `providers.keenable.note` and config `free_tier_guard.keenable.live_run_ruling`, and opens after #36 merges. The wording "opens after #36 and #41 merge" in the meta cycle docs is stale and superseded by this. Each live run needs Fred's go per run (agent-asserted; spec R24/R25 record an earlier go relayed via the CEO).
