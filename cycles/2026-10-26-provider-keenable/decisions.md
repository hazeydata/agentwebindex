# Decisions: AWI-PROVIDER-KEENABLE-001 (2026-10-26)

## Publishing permission (AC17)
Fred's 7:35 AM ruling, as the CEO relayed it:
> "Yes, the CEO's X post is enough permission to publish Keenable."

The terms clauses it covers, from https://keenable.ai/terms (Date of Last Revision: October 3, 2026):
> "provide to any third party the results of any benchmark tests of the Service."
>
> "distribute or otherwise disclose the API Output to any third parties on a standalone basis."

Written permission: the @styskin post https://x.com/styskin/status/2114208595525144970 (2026-10-25 00:13:03 EDT / 04:13:03 UTC):
> "@HazeyDataFred Please include @KeenableAI into your tests."

The post couldn't be archived (archive.org save returned HTTP 523; no snapshot). The saved public X API read is the evidence: `evidence/posts-api.json.txt` (copied from the off-repo evidence store `agentwebindex-evidence/vendor-x/posts-api.json.txt`).

Archive retry (Spec R4 (3), AC9), one attempt each, times America/Toronto:
- 2026-10-26 08:45:50 EDT: `https://web.archive.org/save/https://x.com/styskin/status/2114208595525144970` returned HTTP 523, no snapshot (the first attempt on Oct 26 also returned 523).
- 2026-10-26 08:45:50 EDT: archive.today (`https://archive.ph/submit/`) returned HTTP 429, not archived.
- Both failed, so the evidence is the saved API read (`evidence/posts-api.json.txt`) plus a direct HTML capture of the post URL taken 2026-10-26 08:45:51 EDT (HTTP 200, 121,098 bytes). The og/twitter meta lines are copied verbatim to `evidence/post-capture-og.html.txt`; they show the author "Andrey Styskin (@styskin)" and the quote. The full HTML (sha256 fe771d260d03c39157e0a23132c82001d143eab8037e0c4ed332b8dc208fb170) stays off-repo in `agentwebindex-evidence/vendor-x/` because the secret scanners flag its base64 ids. `evidence/SHA256SUMS` lists the in-repo evidence files.
- The record's sender is "@styskin (Andrey Styskin, CEO and co-founder, Keenable)" (R23 format, Spec R4 (3)). quote and evidence_path are both non-empty (R26).

## Open questions closed
- **OQ-K4, closed (CEO):** scores and metrics only. No raw Keenable result URLs or snippets in any public output (AC23; config `publish_raw_results: false`; run-log lines keep `results_count` and the response hash).
- **OQ-K5, closed (CEO ruling):** the terms' competitor clause ("No employee, independent contractor, agent, or affiliate of any competing company developing and/or providing an AI-powered web index ...") does not apply: HazeyData benchmarks indexes; it doesn't build one.
- **OQ-K2 (free tier only):** `paid_usage_allowed: false`. HTTP 402, a payment-required error, a usage block or billed units > 0 stops all keenable calls as `stopped_paid_usage` (R1/AC20).

## Fact-check (AC10): confirmed
The fact desk (Lucille) confirmed the post, the identity and the officer role from a primary source: Andrey Styskin is Co-founder & CEO per the keenable.ai team section ("Andrey Styskin    Co-founder & CEO    ex-CEO of Search and Ads at Yandex"; JSON-LD founder jobTitle "Co-founder and CEO", sameAs https://x.com/styskin).
- Fact-check file: `agentwebindex-evidence/vendor-x/fact-check-lucille.md` (off-repo evidence store), copied to `evidence/fact-check-lucille.md`
- keenable.ai archive: https://web.archive.org/web/20261026113537/https://keenable.ai/
- Residual risk (from the fact-check): the role rests on the vendor's own site; blue verification doesn't vet identity; the post has no third-party archive; it is a solicited reply in HazeyData's own thread.

## Phasing
- **K1** (hazeydata/agentwebindex#29): adapter, guards, config and tos entry, all default-closed; the vendor_confirmation record stays null in K1; mocks only.
- **K2 and K3 waited on open questions** (OQ-K4, OQ-K5, the AC10 fact-check, and the endpoint and free-tier limits). They are now closed, so the K2 flip PR (hazeydata/agentwebindex#30; historical: #30 was reverted and the flip re-lands as #36, see below) records the vendor_confirmation and sets `live_run_allowed: true`; `paid_usage_allowed` stays false.
- Endpoint: keyless `POST https://api.keenable.ai/v1/search/public` with `X-Keenable-Title: AgentWebIndex` (docs.keenable.ai). Serial, at most 9/s, under 900 per rolling hour; a 429 gets at most one retry after Retry-After, then `rate_limited`; a 402 stops all calls.
- Pricing: pricing.yaml records $4 per 1,000 requests (pay as you go, https://keenable.ai/pricing) outside `providers`; Keenable stays off Also priced and Prices this cycle (tested).

## Later (not this cycle)
- Styskin's reproduced eval, https://x.com/styskin/status/2114405816669544464: Exa needs `contents.highlights=true`, and the exact-URL judge is Google-biased. Methodology notes for a later spec; not implemented.
- K3 (resolved in code, was stale): `bench/tos_matrix.yaml` `providers.keenable.benchmarking.status` is `permitted_by_ruling` (tos_matrix.yaml:271), and `runner.forbidden_clause` (runner.py:191-194) excludes only a `forbidden` benchmarking or publishing_results clause, so the runner no longer records `excluded_tos_benchmarking` for keenable.
- K3 (resolved in code, was stale): `collectors/benchmark.py keyed_providers()` (benchmark.py:239) picks keyless keenable with no key env whenever `guard.live_allowed(cfg, tos, "keenable")` is true, which it is after this flip. `run_pricing()` (benchmark.py:243-248) adds `keenable_price_record.free_tier` from `pricing.yaml` to the runner's pricing map as a `search` `per_request` entry, only if that `free_tier` is set and keenable is not already in `providers`; it is for run eligibility only, and tables, Prices and Also priced still read `pricing.yaml` `providers`.

## Flip re-land (R7)
- #30 (the K2 flip) was squash-merged as 8173d1a5 into the stacked branch `feat/awi-provider-keenable-001` (#29), not a merged base. Per R7 option (b) it was removed from #29 with a normal revert commit, 7804f749, so #29 landed as code, tests and config only (squash 24dcf9a9 into `feat/awi-provider-contextdev-001`).
- The flip is re-landed with recorded_pr -> #36 (R8.2), as its own data/docs-only PR against the merged base `feat/awi-provider-contextdev-001`, and must pass confirmation_diff flip-only (R13) with rc=0 and get its own SHIP.
- Prevention: from now on, a flip PR targets a merged base, never a stacked feature branch.

## README split (Spec R9, option a)
- README.md:73-74 (the Keenable bullet and the vendor-confirmation gate bullet) stays stale in #36. It is fixed in a follow-up PR (number TBD, owner implement), docs-only, against `feat/awi-provider-contextdev-001`, opened after #36 merges. Split forced by R13: its flip-only allowlist rejects README in a flip PR (Spec R9). That README PR must merge before the chain merges to main.

## R36: #36 retargeted to main (2026-10-26)
- Ruling: Spec R36, relayed by chief at 11:59 PM ET, picked option (2). #36 is retargeted from `feat/awi-provider-contextdev-001` to `main` (`gh pr edit 36 --base main`), and main `8fc842223f10bc30e555e347d43b945716036bf1` was merged into this branch with a normal merge commit (no rebase, no force-push). #36 is the R33 (2c) keenable flip into main. Sequence (2b contextdev #41, then 2c keenable) approved by Lucille 2 (AWI owner) at 11:52 PM ET via chief; agent-asserted.
- Merge conflicts (bench/config.yaml, bench/tos_matrix.yaml, bench/tests/test_bench.py) came from #27's branch history, not from #36's own diff. They were resolved to main's content plus only #36's keenable confirmation fields. The diff vs main is flip-only: keenable `live_run_allowed` and `vendor_confirmation` in config.yaml, keenable `vendor_confirmation` in tos_matrix.yaml, and this cycle folder (docs, evidence, SHA256SUMS). `recorded_pr` stays https://github.com/hazeydata/agentwebindex/pull/36.
- Supersedes the "README split" entry above as to base: the README follow-up PR targets `main`, not `feat/awi-provider-contextdev-001`, and opens after #36 merges. No README lines are in #36 (R13 rejects them in a flip PR).
- Merges only on its own Weatherman SHIP. Live runs still need Fred's go.

## R37: re-sync after #41 and #42 merged (2026-10-27)
- #41 merged into main as `9dd16aac5a0e97b9b4cfe5beb7d186cb14c88cb5`, and #42 (the contextdev docs follow-up, R37.1) merged as `fe369f6c79f25b63bd29a0e5a79fd130b563f3ee`, both as merge commits. R37 order: the #36 re-sync comes after #42.
- Main `fe369f6c` was merged into this branch with a normal merge commit. It merged cleanly; the diff vs main is still the 8 keenable files (config.yaml and tos_matrix.yaml confirmation fields, plus this folder).
- Keenable docs follow-up: follow-up PR (number TBD, owner implement); split forced by R13. It opens after #36 merges; this supersedes the "opens after #36 and #41 merge" wording in the meta cycle docs. Until it merges, nothing else lands on main (R37.1).
- recorded_pr hand check (R37.2): config and tos both have https://github.com/hazeydata/agentwebindex/pull/36, this PR's own URL.
- Fred's-go wording, from now on: "Each live run needs Fred's go per run (agent-asserted; spec R24/R25 record an earlier go relayed via the CEO)." It supersedes "Live runs still need Fred's go" above.

## R37.1: keenable docs follow-up #43 (2026-10-27)
- #36 squash-merged into main as `ad262f1c08cfc6909d19f813f3d315a1353db9e0` (12:52 AM ET). The squash was chosen for Kitty t2: `343ac530` would otherwise have brought the 4 uuid lines in again.
- The keenable docs follow-up is hazeydata/agentwebindex#43, from main `ad262f1c`. It replaces "follow-up PR (number TBD, owner implement)" in the two entries above, which stay as written (append-only). It changes README.md, bench/README.md, SPEC-bench.md, tos_matrix `providers.keenable.note` and config `free_tier_guard.keenable.live_run_ruling`, plus this folder. It changes no code, tests or gate values. Kitty, then its own Weatherman SHIP; nothing else lands on main until it merges.
- Docs vs code at `ad262f1c`: `guard.live_allowed` is true for both contextdev and keenable. `gates.publishable` is false for contextdev (`publish_hold`, AC23) and true for keenable (not held; AC23 does not apply to it). The docs say keenable is publishable and that no keenable results exist yet.
- Keenable is keyless (`guard.KEYLESS`): `keyed_providers()` selects it with an empty env once the gate is open. The `benchmark` workflow's state is `disabled_manually` in the repo's Actions settings (read with gh api on 2026-10-27), so no scheduled run calls it. This is not enforced in code. Logged as still broken (owner Fred).
- Still broken, flagged by QG: the 4 uuid lines from #31 (`286991a4`) in `bench/runs/2026-10-26/apify/news_2026-44.jsonl` are on main. Removing them needs a history rewrite, which is Fred's call (data owner).

## Kitty t1 on #43; benchmark workflow ruling (2026-10-27)
- Lucille 2 (AWI owner) ruled that the `benchmark` workflow stays disabled (`disabled_manually` in the repo's Actions settings) until a per-run go. This is agent-asserted. Each live run needs Fred's go per run (agent-asserted; spec R24/R25 record an earlier go relayed via the CEO).
- Kitty t1 marked #43 @`bef42e25` NOT READY. The fix commit on #43 makes these changes:
  - It updates the stale keenable lines in the contextdev cycle summary.md (keenable open since `ad262f1c`, keyless, publishable, workflow disabled) and that summary's #42/#43 next action and links.
  - It labels the Fred's-go line in this folder's summary.md.
  - It removes the K1 pre-flip wording from tos `providers.keenable.note` and config `free_tier_guard.keenable.live_run_ruling`; text only, gate values unchanged.
  - It replaces "mocks only" and "ships default-closed" in README.md, bench/README.md and SPEC-bench.md with the post-flip wording.

## Stale K1 code comments fixed (2026-10-27)
- Kitty and Weatherman flagged on #43 that `bench/awibench/guard.py:13` still said keenable is "mocks only, never called live in K1". The same pre-flip wording was also in `collectors/benchmark.py:210`. Both are rewritten to the post-flip state: open since #36, keyless and publishable, with scheduled runs stopped only by the benchmark workflow being `disabled_manually` in the repo's Actions settings (not in code), and no live keenable call yet. Live runs are covered by Fred's Oct 26 go within spend caps (R24, R39); there is no per-run go.
- Comments and docstrings only. No code behaviour, tests or gate values change. Neither file is in `confirmation_diff.PROTECTED`.
- Test-file comments that describe K1-era fixtures (`bench/tests/test_bench.py`, the K1 classes) are left as written: they describe what those tests check, and a test edit is out of scope here.

## Spec R39: live runs covered by Fred's Oct 26 go (2026-10-27)
- Source: Fred, Oct 27 2026 1:32 AM ET, relayed by chief: "No, my Oct 26 go covers AWI live runs within the spend caps."
- Live runs: covered by Fred's Oct 26 go within spend caps (R24, R39). No per-run go. R39 supersedes R38 and the earlier wording "Each live run needs Fred's go per run (agent-asserted; spec R24/R25 record an earlier go relayed via the CEO)" above (lines kept as written, append-only), and the "until a per-run go" part of the Lucille 2 entry above.
- Workflow and publish status: superseded by the next entry (AWI-KEENABLE-HOLD cancelled; benchmark re-enable approved, sequenced after paper desk R25 reaches Kitty).
- The per-run lines in README.md, bench/README.md, SPEC-bench.md, config `live_run_ruling` (contextdev and keenable), both cycle summary.md files, the `guard.py` docstring and the `collectors/benchmark.py` comment now use the R39 wording (same PR, docs, comments and config note strings only).

## Keenable hold cancelled; benchmark re-enable approved (2026-10-27)
- **Lucille 2 (Side Hustle CEO), 1:33 AM ET Oct 27, agent-asserted:** AWI-KEENABLE-HOLD is cancelled. Keenable is NOT added to `publish_hold`, and the cap guards are unchanged. This supersedes "keenable's publish status is pending a Side Hustle CEO ruling".
- **Fred, 1:36 AM ET Oct 27 (spec R40):** "Yes, re-enable the AWI benchmark workflow to run live on schedule within the caps." The re-enable is sequenced after paper desk R25 reaches Kitty. Until then the workflow is still `disabled_manually` in the repo's Actions settings. Any run over a cap or adding a new provider stops and goes back to Lucille 2. This supersedes "the `benchmark` workflow stays disabled" and the "until a per-run go" part of the Lucille 2 entry above.
- Docs are written to stay true before and after the re-enable: "approved for re-enable (spec R40); state as of this PR: `disabled_manually`". This PR does not enable the workflow.

## Spec R40 recorded; Kitty t1 on #44 (2026-10-27)
- Spec recorded Fred's 1:36 AM ET answer ("Yes, re-enable the AWI benchmark workflow to run live on schedule within the caps.") as contextdev spec R40. Every mention of that approval in this PR now cites "(spec R40)" in place of the unsourced "relayed by chief" labels Kitty t1 flagged. R40.3: re-enable is sequenced after paper-desk spec-002 R25 reaches Kitty; a run over a cap or adding a new provider stops and returns to Side Hustle CEO. State as of this PR: `disabled_manually`; this PR does not enable the workflow.

## Spec R41 wording fix (2026-10-27)
- The R40 entry above says "Any run over a cap or adding a new provider stops and goes back to Lucille 2." as current behaviour. It is a requirement (spec R40.3), detailed in spec R41 (R41.1 pre-run over-cap projection; R41.2 unknown or uncapped provider stop), built in hazeydata/agentwebindex#45 and not on main until #45 merges. Spec R41.3: the workflow stays disabled until R40.4 and R41 pass with Kitty and a Weatherman SHIP. The entry above stays as written (append-only); README.md, bench/README.md, guard.py and both summaries are reworded to match.
- The README fixes Kitty t1 found (5 crons, `MONTHLY_CAP_USD` hardcoded `"0"`, push to main / manual trigger / test step, 8 adapters) are owned by #45, not this PR.
