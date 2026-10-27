# AWI-PROVIDER-CONTEXTDEV-001: cycle summary

Current state (2026-10-26, R33/R34): the context.dev live gate is **closed**.

- bench/config.yaml: `free_tier_guard.contextdev.live_run_allowed` is `false` and `vendor_confirmation` is `null`, so every run records `skipped_no_vendor_confirmation` and makes zero context.dev calls.
- bench/tos_matrix.yaml: `providers.contextdev.vendor_confirmation_required` is `true` and `vendor_confirmation` is `null`, so context.dev is not published.
- Keenable is also closed (`live_run_allowed: false`, `vendor_confirmation: null`) until its K2 flip PR.
- The vendor record (Yahia Bakour's public X reply, evidence in `evidence/`) is described in decisions.md. contextdev re-opens only in its own flip-only PR (number TBD), which re-adds that record and sets `live_run_allowed` to `true`.
