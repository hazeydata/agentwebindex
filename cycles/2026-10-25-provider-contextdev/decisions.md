# AWI-PROVIDER-CONTEXTDEV-001: context.dev vendor confirmation flip (R23, AC27b-d)

This PR records the vendor's written request and opens the context.dev live gate. It is flip-only (R13/AC24e): the confirmation fields in bench/config.yaml and bench/tos_matrix.yaml for contextdev, this file and the evidence file. It does not merge on its own; it is routed to Kitty and Weatherman. Stacked on hazeydata/agentwebindex#27.

## Record
- date: 2026-10-25 (post time 2026-10-25T04:27:40Z = 00:27:40 ET).
- sender: "@mynameisyahia (Yahia Bakour, Founder and CEO, Context.dev)" (R23 "@handle (Name, title)"; same string in bench/config.yaml and bench/tos_matrix.yaml).
- quote (verbatim from the saved API read): "@HazeyDataFred Hello! Can you add https://t.co/TjSwtUVs0K as well?"
- What it is: the vendor's CEO **asked to be added** to the weekly benchmark, in a reply to HazeyData's post 2114114836904690156. The word "test" is not in the post.
- evidence_path: cycles/2026-10-25-provider-contextdev/evidence/posts-api.json.txt: the JSON body of fact's saved public X API read, with the tool-wrapper and rate-limit lines removed (JSON unchanged; SHA256 bdbbe10be1353a73d8652b711ba75ed7bcb5a96869156dac43edc4b7a2f9e41e, listed in evidence/SHA256SUMS). Fact's unstripped original: SHA256 7a195d696344624a58cb001a8296481d9bbe42b0a253d9737741e17dbb155da2.
- **R23 archive: R27 fallback applied (no SHIP block).** Post https://x.com/mynameisyahia/status/2114212271258038294. Failed attempts: web.archive.org save, 2 tries earlier on 2026-10-26 (times not recorded) and HTTP 523 at 2026-10-26 09:57:55, 09:58:05 and 09:58:15 ET (5 Wayback tries in total); Wayback availability API at 09:58:26 ET: no snapshot; archive.today submit, tried once at 2026-10-26 10:21:55 ET: HTTP 429 (rate-limit/CAPTCHA page, no archive URL). Per R27(3) the evidence is the cleaned X API JSON `evidence/posts-api.json.txt` (the record's evidence_path) plus an HTML capture of the post from the public oEmbed endpoint, no login (`evidence/post-embed.html`, fetched 10:22:12 ET), both listed in evidence/SHA256SUMS. quote and evidence_path are non-empty. **Wayback backfill pending**: one Wayback retry after merge (R27(5), owner implement); on success a docs-only PR adds the URL to the record.
- recorded_pr: https://github.com/hazeydata/agentwebindex/pull/38, this re-land PR (chief ruling, 2026-10-26 9:37 PM ET). The record was first written with #28, which was merged early and reverted by #39.

## Fact-check (AC27b passed)
- Fact's file: fact-check-lucille.md in the vendor-x evidence folder (SHA256 a32245986d79817824dad0395ed797928aa26d14bfaf7ec1a2a5c14b57e8e7b5).
- Officer role from a non-X source: the vendor's author page says "Yahia Bakour is the founder and CEO of Context.dev", with JSON-LD sameAs https://x.com/mynameisyahia. Archive snapshot: https://web.archive.org/web/20261026113459/https://www.context.dev/authors/yahia-bakour
- Every t.co link in the post and the profile resolves to context.dev (checked independently by fact and by chief, 2026-10-26, no cookies): t.co/TjSwtUVs0K and t.co/lqMcRkRhm1 -> www.context.dev/; t.co/KQex6aZnDX -> link.context.dev/xp -> www.context.dev/.
- Residual risk (from fact): blue verification is paid, not identity-vetted; the role rests on the vendor's own site; the post has no third-party archive; it is a solicited inclusion request in HazeyData's own thread.

## R30: live_allowed test made fixture-strict for contextdev (2026-10-26)
- Problem: `K1Committed.test_ac3_live_allowed_generalized` asserted `live_allowed(CFG, TOS, "contextdev")` (and the default id) is False on the committed config. #28 commits the contextdev confirmation, which makes it True, so the test failed on #28.
- Ruling (R30): check contextdev on fixtures, strict both ways: no confirmation record gives False; a valid record gives True. No skip or xfail. Keenable assertions unchanged. Test files and this entry only; no PROTECTED file (`confirmation_diff.PROTECTED`, e.g. gates.py) and no config (config.yaml, tos_matrix.yaml) changed.
- Order: this test-only PR gets its own SHIP and merge into feat/awi-provider-contextdev-001 first. Then #28 re-syncs on the new base, with `python3 -m awibench.confirmation_diff origin/feat/awi-provider-contextdev-001 HEAD` rc=0 and the full suite green, before #28 proceeds.

## R33/R34: gate re-closed for #27 (2026-10-26)
- Gate re-closed for #27 per R33; contextdev re-opens only in its flip PR (number TBD).
- State on this branch: bench/config.yaml `free_tier_guard.contextdev.live_run_allowed` is `false` and `vendor_confirmation` is `null`; bench/tos_matrix.yaml `providers.contextdev.vendor_confirmation` is `null`. Keenable stays closed (`false`/`null`). Lines 3 and 21 above describe the #28/#38 flip and are kept as written (append-only).

## R33 (2b): contextdev flip-only PR into main (2026-10-26)
- #27 merged into main as squash `8fc842223f10bc30e555e347d43b945716036bf1` with both gates closed. Per R33 (2b), contextdev re-lands in its own flip-only PR, hazeydata/agentwebindex#41 from `flip/awi-r33-contextdev`, branched from `8fc84222`.
- Change: bench/config.yaml `free_tier_guard.contextdev.live_run_allowed` false -> true and `vendor_confirmation` null -> the record; bench/tos_matrix.yaml `providers.contextdev.vendor_confirmation` null -> the same record. The record is the one #27's revert `e50a9721` removed, unchanged except `recorded_pr`, now https://github.com/hazeydata/agentwebindex/pull/41 (its own PR, per R33). Plus this file and summary.md. No code, test or other file changes.
- Approval: Lucille 2 (AWI owner) approved the sequence (2b then 2c) at 11:52 PM ET on 2026-10-26, via chief; agent-asserted, wording not on file.
- Gate: merges only on its own Weatherman SHIP, with confirmation-diff-base green at its exact head. Live runs still need Fred's go.
- Keenable (R33 2c) is not part of this PR. Its record was never on #27 (#27's revert left keenable false/null; keenable's record lives on the open #36, whose evidence files under cycles/2026-10-26-provider-keenable/ are not on main), so a keenable flip needs a ruling on its record source and evidence first.

## R37.1: contextdev docs follow-up (2026-10-27)
- #41 merged into main as merge commit `9dd16aac5a0e97b9b4cfe5beb7d186cb14c88cb5` (12:11 AM ET). Base runs per R33.4, green at `a98dc1d6`: base-judged 40446997862, advisory 40446997693.
- Spec R37.1 (12:12 AM ET, asked by quality-gate): after each flip PR merges, a docs-only PR into main updates README, bench/README and SPEC-bench for that provider, and nothing else lands on main until it merges. The contextdev follow-up is hazeydata/agentwebindex#42, which replaces the "docs-only PR after #41 merges" TBD above. It changes README.md, bench/README.md, SPEC-bench.md, tos_matrix `providers.contextdev.note`, config `free_tier_guard.contextdev.live_run_ruling` and this folder's summary.md and decisions.md. It changes no code, no tests and no gate values. Keenable stays described as closed.
- R36.5: R32.2 did not specify a merge method; the #27 squash is a note, not a violation.

## Kitty t1 on #42 (2026-10-27)
- The line "Gate re-closed for #27 per R33; contextdev re-opens only in its flip PR (number TBD)." under "R33/R34" is superseded: the flip PRs are #41 (contextdev, merged as `9dd16aac`) and #36 (keenable, open). The old line stays as written (append-only).
- Keenable terms wording: README.md, bench/README.md and SPEC-bench.md said `benchmarking: forbidden`; the committed `providers.keenable.benchmarking.status` is `permitted_by_ruling` (as bench/README.md's terms-clause line already said). Fixed to match the code.
- Fred's-go wording, used everywhere from now: "Each live run needs Fred's go per run (agent-asserted; spec R24/R25 record an earlier go relayed via the CEO)." It replaces earlier wordings in this folder (such as "Live runs still need Fred's go" in the #41 entry), which read as both pending and already given.

## Spec R39: live runs covered by Fred's Oct 26 go (2026-10-27)
- Source: Fred, Oct 27 2026 1:32 AM ET, relayed by chief: "No, my Oct 26 go covers AWI live runs within the spend caps."
- Live runs: covered by Fred's Oct 26 go within spend caps (R24, R39). No per-run go. This supersedes the per-run Fred's-go wording above (kept as written, append-only). The `benchmark` workflow stays disabled, and keenable's publish status is pending a Side Hustle CEO ruling.
