# AWI-RUN-2026-10-26, Apify part (2026-10-26)

Approved by Fred on 2026-10-26 at 7:31 AM ET, as relayed by the Side Hustle CEO. Both runs were Apify only (`--only-providers apify`), with calls made one at a time (concurrency 1) and the meter guard reading the live Apify usage meter before each call. Bright Data and Firecrawl were not called.

| Run | Set (n) | Actor / config | Calls | Data calls | Pass | Fail | Timeouts | HTTP errors | Status |
|---|---|---|---|---|---|---|---|---|---|
| webfetch (08:01-08:02 ET) | newsfetch_2026-44 (21) | apify/web-fetch, `web-fetch-text` | 21 | 21 | 19 | 2 | 0 | 0 | ran (full set) |
| rag (08:02-08:13 ET) | news_2026-44 (21) | apify/rag-web-browser, `rag-raw-http-t240` | 21 | 21 | 16 | 5 | 0 | 0 | ran (full set) |

- Web Fetch: median latency 2.0 s, max 15.5 s. Both fails, `news-2026-W44-009` (adrs.systems) and `news-2026-W44-012` (github.com/notaroomba/keybytes), returned HTTP 200 with page text that does not contain the Hacker News headline. That is a limit of the grader on HN rows, not a fetch error.
- RAG Web Browser with the 240 s timeout: median latency 26.6 s, max 102.9 s. There were no timeouts, and no result URLs were Google `goto` redirects. All 5 fails are `domain not in top5`.
- Spend: the Apify meter went from $3.0203 (read just before the runs) to $3.9026 (read at 08:14 ET), so the runs cost $0.88 of the $5 Free-plan credit. Per run the meter moved $0.0255 for webfetch and $0.8563 for rag (bench/spend/apify-meter-2026-10.json). The ledger `apify_usd_cents` went from 184.358 to 235.505. The meter now sits $0.10 below the $4.00 stop, so the next Apify call this month will be refused (stale_budget).
- Not committed: `data/benchmarks/latest.json`, `_run-offset0.json` and `_skips-offset0.json`. Other providers' parts of today's run write the same files, so publishing them is left to a `--rebuild-only 2026-10-26` after all the parts are merged. The run summaries are `run-*-summary.json` in this folder.
