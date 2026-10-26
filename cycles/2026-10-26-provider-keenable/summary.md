# AWI-PROVIDER-KEENABLE-001 K2 flip (2026-10-26)

Flip-only PR (AC24e, #36). Re-lands the K2 flip on the merged base `feat/awi-provider-contextdev-001` after K1 (#29) merged as squash 24dcf9a9. Changes:
- `bench/config.yaml` `free_tier_guard.keenable.live_run_allowed`: false -> true; `vendor_confirmation`: null -> the record below. `paid_usage_allowed` stays false.
- `bench/tos_matrix.yaml` `providers.keenable.vendor_confirmation`: null -> the same record.
- Record: date "2026-10-25", sender "@styskin (Andrey Styskin, CEO and co-founder, Keenable)", quote "Please include @KeenableAI into your tests.", evidence_path `cycles/2026-10-26-provider-keenable/evidence/posts-api.json.txt` (the saved public X API read; archive.org 523 and archive.today 429 on the Oct 26 08:45 EDT retry; HTML capture in `evidence/post-capture-og.html.txt`, SHA256SUMS), recorded_pr https://github.com/hazeydata/agentwebindex/pull/36 (this PR; per Spec Keenable R8, changed from #30).
- Evidence: `evidence/posts-api.json.txt`. Decisions and the fact-check citation: decisions.md.

No live calls were made. Merge needs a Weatherman SHIP at the exact head and chief merging (AC11).
