# AWI-PROVIDER-KEENABLE-001 K2 flip (2026-10-26)

Flip-only PR (AC24e, #36). Re-lands the K2 flip. **R36 (2026-10-26 11:59 PM ET, Spec via chief):** #36 is retargeted from `feat/awi-provider-contextdev-001` (merged into main by #27, squash 8fc84222) to `main`, and main was merged into its branch with a normal merge commit (no rebase, no force-push). This is R33 (2c), the keenable flip into main. Changes vs main:
- `bench/config.yaml` `free_tier_guard.keenable.live_run_allowed`: false -> true; `vendor_confirmation`: null -> the record below. `paid_usage_allowed` stays false.
- `bench/tos_matrix.yaml` `providers.keenable.vendor_confirmation`: null -> the same record.
- Record: date "2026-10-25", sender "@styskin (Andrey Styskin, CEO and co-founder, Keenable)", quote "Please include @KeenableAI into your tests.", evidence_path `cycles/2026-10-26-provider-keenable/evidence/posts-api.json.txt` (the saved public X API read; archive.org 523 and archive.today 429 on the Oct 26 08:45 EDT retry; HTML capture in `evidence/post-capture-og.html.txt`, SHA256SUMS), recorded_pr https://github.com/hazeydata/agentwebindex/pull/36 (this PR; per Spec Keenable R8, changed from #30).
- Evidence: `evidence/posts-api.json.txt`. Decisions and the fact-check citation: decisions.md.

No live calls were made. Merge needs its own Weatherman SHIP at the exact head, confirmation-diff-base green at that head, and chief merging (AC11). Live runs still need Fred's go.

**Still broken:**
- README.md (the Keenable bullet and the vendor-confirmation gate bullet) and the matching bench/README.md and SPEC-bench.md text still say keenable is `false`/`null` and "both with `vendor_confirmation: null`". R13 rejects README in a flip PR, so a docs-only PR against `main` fixes them after #36 merges (owner: implement). The contextdev flip, hazeydata/agentwebindex#41, has the same follow-up.
- The tos_matrix `providers.keenable.note` and config `live_run_ruling` text describe the closed state. They are not confirmation fields, so the same docs PR fixes them.

**Docs match code:** yes for this folder; no for README.md, bench/README.md and SPEC-bench.md (above).

**Update (2026-10-27, R37 re-sync):** #41 (contextdev flip) merged into main as merge commit `9dd16aac`, and #42 (contextdev docs follow-up, R37.1) merged as merge commit `fe369f6c`. Main `fe369f6c` was merged into this branch with a normal merge commit (no rebase, no force-push). The diff vs main is still only the 8 keenable files. Keenable docs follow-up: follow-up PR (number TBD, owner implement); split forced by R13. It updates README.md, bench/README.md, SPEC-bench.md, tos_matrix `providers.keenable.note` and config `free_tier_guard.keenable.live_run_ruling`, and opens after #36 merges. The wording "opens after #36 and #41 merge" in the meta cycle docs is stale and superseded by this. Each live run needs Fred's go per run (agent-asserted; spec R24/R25 record an earlier go relayed via the CEO).
