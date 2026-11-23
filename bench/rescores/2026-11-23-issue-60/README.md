# AWI-ISSUE-60 rescore: evergreen search, before vs after (Nov 23, 2026)

Issue: https://github.com/hazeydata/agentwebindex/issues/60 (Kes, who disclosed he works at Exa).
Before = grader `canon-2026-10-20` at agentwebindex main `d862007`. After = grader `canon-2026-11-23` on branch `awi-issue-60`.
Offline only: no provider calls, $0. The rescore tool blocks `urlopen` and socket connects. The only network used in this cycle was the one-time resolver run that wrote `bench/sets/url_redirects.json` (plain GETs to the 45 expected URLs, no auth).

## Our logged results, per provider and check type

| Provider (run) | Check | Before | After | After, no redirect log |
|---|---|---|---|---|
| Exa (Oct 23) | url_exact | 23/42 | **42/42** | 40/42 |
| Exa (Oct 23) | snippet_contains | 3/43 | 3/43 (logged snippets are null; needs a fresh run) | 3/43 |
| Exa (Oct 23) | url_domain | 14/15 | 14/15 | 14/15 |
| Exa (Oct 23) | total | 40/100 | **59/100** | 57/100 |
| Tavily (Oct 23) | url_exact | 18/42 | **36/42** | 24/42 |
| Tavily (Oct 23) | snippet_contains | 38/43 | 38/43 | 38/43 |
| Tavily (Oct 23) | url_domain | 13/15 | 13/15 | 13/15 |
| Tavily (Oct 23) | total | 69/100 | **87/100** | 75/100 |
| Firecrawl (Nov 2, 40-item trimmed run) | url_exact | 6/9 | 8/9 | 6/9 |
| Firecrawl (Nov 2) | snippet_contains | 26/30 | 25/30 (300-char cap; se-fr-013 matched past char 300) | 25/30 |
| Firecrawl (Nov 2) | url_domain | 1/1 | 1/1 | 1/1 |
| Firecrawl (Nov 2) | total | 33/40 | 34/40 | 32/40 |

No other provider has logged search_evergreen results. Apify, TinyFish, context.dev and Keenable have no evergreen logs, so there is nothing to rescore for them.
The "no redirect log" column is the ruled awi-method-v2 rule set alone (R2 FINAL / R3, Oct 26). It reproduces fact's AWI-KRAB-CHECK-002 numbers (Exa 57, Tavily 75) and flip lists exactly, and that is a golden test.

Per-item flips with the rule that fired are in `after.csv` (`rule_ids`, `matched_url`). Exa gains 19: 12 M01 (RFC .html or datatracker copy), 6 RD (the expected link's logged redirect, 5 RFC `/info/` + MDN), and 1 R07b (`?highlight=`). Tavily gains 18: 16 RD and 2 M01 (tools.ietf.org). Firecrawl gains 2 RD and loses 1 to the snippet cap.

## Kes's four numbers

| Claim in #60 | Ours | Verdict |
|---|---|---|
| Exa url_exact 23/42 → 41/42 | 23/42 → 42/42 | **before confirmed; after differs (+1)** |
| Tavily url_exact 18/42 → 34/42 | 18/42 → 36/42 | **before confirmed; after differs (+2)** |
| Exa snippet_contains 3/43 | 3/43 | **confirmed** |
| Tavily snippet_contains 38/43 | 38/43 | **confirmed** |

The gap is fully explained. A rule set of RFC .html/.txt variants, rfc-editor.org `/info/` pages and query-only differences, without resolving moved pages, gives exactly Exa 41/42 and Tavily 34/42 (rule-subset ablation: agentwebindex-meta `cycles/2026-11-23-awi-issue-60/evidence/kes-ablation.txt`). We also resolve the expected link's own redirect. That adds Exa se-doc-006 (MDN flex-direction moved to `/Web/CSS/Reference/Properties/flex-direction`) and Tavily se-doc-001 (Python `library/stdtypes.html` now 301s to `builtins/stdtypes.html`) plus se-doc-006. Kes listed the MDN move, so his 41 most likely just didn't count it.
Denominators: the set has 100 rows, made up of 42 url_exact, 43 snippet_contains and 15 url_domain. 42 vs 43 is two different check types, not a dropped row. No row errored or was skipped in these logs (`not_graded=0`).

## Snippets (fix 1): after-state needs a fresh Exa run
Logged Exa results carry `snippet: null`, so no offline rescore can show the fix. Indicative only (not our run, not publishable as ours, AC 13a): third-party raw logs from @styskin's KrabArena artifact (Oct 25, `krab-check/captures/krab-artifact/results`) give Exa with highlights snippet_contains 42/43 uncapped and **39/43 under our new 300-char budget**, Exa without highlights 5/43, and Tavily 40/43.
A fresh Exa run of the 43 snippet_contains items (43 searches against the free $10/month credit; highlights don't bill extra on /search per exa.ai/pricing, read Nov 23) is a leftover, because provider keys aren't available to this executor.

## Week 47 (and every news week)
Regrading every committed news log (W42–W48, all providers) under the old and new grader flips **0** news lines, so the published Week 47 numbers don't change. The only visible change on a future rebuild is the `grader_version` label (`canon-2026-10-20` → `canon-2026-11-23`). The redirect log covers only search_evergreen URLs, and none of M01/R07b/R08/R09/R10/M02 fires on a news line.

## Commands (run from the agentwebindex repo root)
```
L="--set search_evergreen --set-file bench/sets/search_evergreen.jsonl --log exa=bench/runs/2026-10-23/exa/search_evergreen.jsonl --log tavily=bench/runs/2026-10-23/tavily/search_evergreen.jsonl --log firecrawl=bench/runs/2026-11-02/firecrawl/search_evergreen.jsonl"
python3 bench/tools/rescore.py --bench-dir <origin/main checkout>/bench $L --out-csv before.csv     # before (main d862007)
python3 bench/tools/rescore.py --bench-dir bench $L --out-csv after.csv                            # after
python3 bench/tools/rescore.py --bench-dir bench $L --no-redirects --out-csv after-no-redirects.csv
```

## Input file hashes (sha256)
- bench/sets/search_evergreen.jsonl `6b488a955e0896d91fd0db99b1936c38721f79e68c02cef0589e70664874a527`
- bench/runs/2026-10-23/exa/search_evergreen.jsonl `763f894d9c54988f30acb933d8aadc869ed4384ef85f6f2ff74c63f68d7fc7f1`
- bench/runs/2026-10-23/tavily/search_evergreen.jsonl `f7916c56777b7769c7a364e19a414239700855b8a90f131d62be8697fe6093db`
- bench/runs/2026-11-02/firecrawl/search_evergreen.jsonl `f2609edffc5f5c375c20b0037f44197b971c29aa80bbf8c034700e304cb4cdb8`
- bench/sets/url_redirects.json `f0cc702441edaf3b495cea7e1fe6ae20f6a2704837ca1e49a4f35ee159415054` (resolved 2026-11-23 ~19:13 UTC)
- KrabArena (indicative): exa_search_evergreen.jsonl `3e6fcf83…`, diagnostics/exa_without_highlights_original.jsonl `2577e711…`

Outputs: `before.csv` `b75ba096…`, `after.csv` `144a54eb…`, `after-no-redirects.csv` `20f3a39a…` (committed under `bench/rescores/2026-11-23-issue-60/`; reruns are byte-identical).
