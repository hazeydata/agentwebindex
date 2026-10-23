# AWI-EVERGREEN-RUN-001 (2026-10-23), first $0 free-tier run over frozen sets (#21)

- Sets: search_evergreen (100) via Exa + Tavily; scrape_urls (150) via Browserless /content. Firecrawl, Bright Data: not run (out of scope; keys unset for the run).
- Apify: **skipped**. Live meter read $3.02 of $5 before the run (the config `meter_budget.apify` stop is $4.00). That leaves $0.98, which cannot cover 100 RAG-browser queries, so a full set (the only kind that publishes) was impossible. Skipped so the rest of the month's credit stays free for the weekly news runs.
- Exa: the vendor usage meter can't be read through the API (`/usage` and `/v1/usage` return 404), so the ledger was used instead: 73 of 1428 used before the run. 100 calls is a safe count against that.
- Browserless: no usage API is available (only `/meta`), so the ledger was used: 57 of 1000 before the run. The worst case for 150 calls is 2 units each.
- Tavily: GET /usage read plan_usage 78 of 1000 before the run, and still 78 about 13 min after it (the meter appears to lag).
- Raw run logs are in ../exa, ../tavily and ../browserless. Meter readings are in meters-*.json (keys redacted). Stdout and stderr of the run are in run-*.log.
