# AWI-PROVIDER-KEENABLE-001 K2 flip (2026-10-26)

Flip-only PR (AC24e), stacked on K1 (#29). Changes:
- `bench/config.yaml` `free_tier_guard.keenable.live_run_allowed`: false -> true; `vendor_confirmation`: null -> the record below. `paid_usage_allowed` stays false.
- `bench/tos_matrix.yaml` `providers.keenable.vendor_confirmation`: null -> the same record.
- Record: date "2026-10-25", sender "@styskin (Andrey Styskin, CEO and co-founder, Keenable)", quote "@HazeyDataFred Please include @KeenableAI into your tests.", evidence_path `cycles/2026-10-26-provider-keenable/evidence/posts-api.json.txt` (the saved public X API read; archive.org 523 and archive.today 429 on the Oct 26 08:45 EDT retry; HTML capture in `evidence/post-capture-og.html.txt`, SHA256SUMS), recorded_pr this PR.
- Evidence: `evidence/posts-api.json.txt`. Decisions and the fact-check citation: decisions.md.

No live calls were made. Merge needs a Weatherman SHIP at the exact head and chief merging (AC11).
