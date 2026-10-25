# agentwebindex: bench, collectors and data

> Split note: the static site now lives in the separate `agentwebindex-site` repo, which vendors this repo's `data/`. The site sections below describe that repo.

# AgentWebIndex v1 (site + collectors)

Static site (plain HTML/JS + a 40-line Python generator) and two Python collectors (stdlib only, no deps).
Spec: `../PLAN.md`. Nothing here contains hard-coded prices or benchmark numbers; every page shows
"No data yet" until real files exist under `data/`.

## Layout
- `src/` page sources (`_layout.html` wraps each page), `app.js` renders data client-side
- `build.py` → `dist/` (pages + copy of `data/` + `llms.txt`)
- `collectors/providers.json` provider list, pricing URLs, key env-var names
- `collectors/snapshot_prices.py` daily pricing-page snapshot + diff → `data/prices/<id>.json`, `data/prices/history/<id>.jsonl`, `data/prices/index.json`. It skips any provider whose `tos_matrix.yaml` entry fails `gates.vendor_confirmed` (`snapshot_providers()`, AC24g): an unconfirmed vendor's pricing page (Context.dev today) is never fetched and gets no published `data/prices` file.
- `collectors/benchmark.py` harness → `data/benchmarks/latest.json` (+ `smoke-latest.json` / `full-latest.json`)
- `.github/workflows/prices.yml`, `.github/workflows/benchmark.yml`

## Run locally
```
python3 build.py
python3 -m http.server 8765 -d dist     # http://localhost:8765/
python3 collectors/snapshot_prices.py   # fetches each pricing page once (network)
python3 collectors/benchmark.py --mode smoke   # skips any provider whose key env var is unset
python3 build.py                        # rebuild to pick up new data
```

## Deploy to Cloudflare Pages
Connect the repo in Cloudflare Pages (Free): build command `python3 build.py`, output directory `dist`
(set env `PYTHON_VERSION=3.12`). Each data commit from Actions triggers a rebuild. Alternatively
`npx wrangler pages deploy dist`. Not done yet: needs Fred's approval (PLAN §6 #6).

## Actions secrets (all optional; missing = provider skipped)
EXA_API_KEY, TAVILY_API_KEY, BRAVE_API_KEY, LINKUP_API_KEY, PARALLEL_API_KEY, SERPER_API_KEY,
FIRECRAWL_API_KEY, JINA_API_KEY, BRIGHTDATA_API_KEY, APIFY_API_TOKEN, HYPERBROWSER_API_KEY,
SCRAPINGBEE_API_KEY, BROWSERBASE_API_KEY, STEEL_API_KEY, BROWSERLESS_API_KEY, CONTEXT_DEV_API_KEY.
The price snapshot needs no secrets. Both workflows use the built-in `GITHUB_TOKEN` with `contents: write` to commit data.

## Cron (UTC)
- Price snapshot: `17 9 * * *` (daily 09:17 UTC = 05:17 ET during EDT)
- Smoke benchmark: `41 10 * * *` (daily)
- Full benchmark: `23 11 * * 1` (Mondays)

## Known v1 gaps
- Benchmark adapters (`ADAPTERS` in benchmark.py) are empty: providers with a key are recorded as `not_implemented` until adapters are written after signups.
- Test sets `collectors/testsets/{search,scrape,browser}-set-v1.json` aren't authored yet.
- Price extraction is regex over static HTML. JS-rendered pages (e.g. Jina) show 0 amounts; there is no structured per-plan parsing yet.
- No cost-per-1k column yet (needs a mapping from page amounts to per-call prices).

## v1.1 additions
- **Page text out of git:** full text lives only in `data/cache/prices/<id>.txt` (gitignored). The prices workflow restores the previous run's cache via `actions/cache` (for text diffs) and uploads it as artifact `price-page-text-<run_id>` (90 days). Git keeps only `prices_found` and `text_sha256`. `collectors/migrate_text_to_cache.py` did the one-off migration.
- **Spend cap:** set repo variable `MONTHLY_CAP_USD` (default `0` = free-tier-only). Per provider/category, `providers.json` → `costs.<cat>.{cost_per_call_usd, free_calls_per_month, source, verified_at}`, all `null` until verified. Null quota with cap 0, or null cost with cap > 0, means the provider is skipped. Usage is in `data/spend/YYYY-MM.json`.
- **Downloads:** `build.py` writes `dist/data/prices.csv` (one row per amount) and `dist/data/prices.json`, linked from /prices/.
- **Newsletter:** set `beehiiv_embed_url` in `site.config.json` (https only) to render the iframe; leave it empty to keep the disabled "Coming soon" form.
- **Hooks (spec pending):** `load_test_set`, `cost_per_1k`, `freshness_score`, `ADAPTERS` in `collectors/benchmark.py`.

## Benchmark (SPEC-bench v1), see bench/README.md
Spec: [SPEC-bench.md](SPEC-bench.md).
- Sets live in `bench/sets/`: `search_evergreen.jsonl` (100 rows), `scrape_urls.jsonl` (150 rows), `browser_tasks.yaml` (20 tasks) and the weekly `news_YYYY-WW.jsonl` sets, frozen against the manifest named in `bench/config.yaml` and checked by `python -m awibench.validate`. `bench/pricing.yaml` and `bench/tos_matrix.yaml` carry verified values where captured; a value not on the vendor's page stays `NOT CAPTURED` (pricing) or null (ToS) and shows as "n/a".
- Live runs: free tier only, `MONTHLY_CAP_USD` must be `0` (D4). The runner stops a provider before a call would exceed its free quota and marks the run `stale_quota`. Quota usage goes to `data/spend/YYYY-MM-quota.json` and logs to `bench/runs/YYYY-MM-DD/<provider>/<set>.jsonl`. Tables are built from those logs into `data/benchmarks/latest.json`.
- Cron (UTC): news set Mondays 10:00 (06:00 ET during EDT; change it to 11:00 for EST); full run Mondays 11:23; re-probes Mon 17:23 (+6 h), Tue 11:23 (+24 h) and Thu 11:23 (+72 h); smoke run daily 10:41.
- Context.dev (AWI-PROVIDER-CONTEXTDEV-001 spec rev 4, search + scrape, free tier only; plain-text name, no logo): key env `CONTEXT_DEV_API_KEY` (no key = `skipped_no_key`). **Live-run gate (R9):** config `free_tier_guard.contextdev.live_run_allowed` is `false` and `vendor_confirmation` is `null`, so every run records `skipped_no_vendor_confirmation` and makes zero calls (the Monday news job included) until written vendor confirmation is recorded and both are set in a separate PR. **Anchor-window guard:** the 1,000 free credits refill on the org's grant-anchor day (`grant_anchor_day`, unknown = `null`, so the counter never resets); before every attempt `max(usage meter, attempts counter) + the call's credits` must be `<= 900` (`stop_at_credits`), every attempt counts billed or not, and a corrupt, unreadable or locked counter or an unreadable meter stops it as `stale_budget` (fail closed). Calls are serial with `max_concurrent` 1 (cap 1). Credit rule (F4): scrape 1 (2 with browser actions); search 1 per 10 results (rounded up), +1 per 10 each for markdown and highlights. Billing rule (F5): 2xx and 404 bill the credits; validation, 401, 403, 408, 429, 5xx and no response bill 0; a 429 with `Retry-After` is retried once after the wait; 402 or 401 `USAGE_EXCEEDED` is a quota stop (`stale_quota`, no retry). **Publishing:** `publish_hold: ["contextdev"]` keeps it out of `latest.json`, `data/` and `dist/` in this build; the publish gate additionally needs a numeric entry price (P0, not captured: sources conflict) and a terms verdict `allowed` (P1, `unverified`); `providers.json` carries the optional `exclude_from_also_priced: true` (absent = false) so it stays out of the "Also priced" line. Details in bench/README.md.
- Tests: `./run_tests.sh` (pytest `tests/test_bench.py`, the mock dry run, the secrets scan); or `cd bench && python -m pytest tests/test_bench.py` (mock only, pytest `tmp_path`) and `python tests/dry_run.py`, which runs the full mock pipeline in a temp dir that it removes.

- Vendor-confirmation publish gate (R10, AC24a-24g): a `tos_matrix.yaml` entry with `vendor_confirmation_required: true` publishes nothing (no table row, freshness, `not_run_yet`, skip, run-summary entry, and no `exclusions` entry in `latest.json`, no data/CSV/dist, no Prices row) and is never called (`skipped_no_vendor_confirmation`) until its `vendor_confirmation` record holds a `date`, a `sender` and a `quote` or `evidence_path`. There is ONE implementation, `gates.vendor_confirmed(tos_entry)` in `bench/awibench/gates.py`; `tables.build`/`held_providers`, the runner, `collectors/benchmark.py` and `guard.live_allowed(cfg, tos)` all use it, and `gates.publishable(pid, pricing, tos)` adds the other gates (AC23 for contextdev). Providers without the flag are unaffected. Required today: `contextdev` and `keenable`, both with `vendor_confirmation: null`. Keenable is gate-only: a `tos_matrix.yaml` entry (its terms bar sharing benchmark results) and nothing else, with no `providers.json`, `pricing.yaml` or config entry, no adapter and no live call. Recording a reply (`vendor_confirmation`, and `live_run_allowed` for contextdev) must go in its own data/docs-only PR: `python -m awibench.confirmation_diff [BASE]` (run from `bench/`, default `origin/main`) fails when a diff changes a confirmation value together with code changes. It runs on every pull request (`.github/workflows/confirmation-diff.yml`, base `origin/<base branch>`) and in `run_tests.sh` (base `AWI_CONFIRMATION_BASE`, default `origin/main`; a missing base fails). Bootstrap allowance: added lines that set the closed default (`vendor_confirmation: null`, `live_run_allowed: false`, a record field `null`) do not count while no entry in `tos_matrix.yaml` or `config.yaml` holds any non-null `vendor_confirmation` or `live_run_allowed: true`, so the PR that introduces a gated provider can carry code; removed lines, real values, or a tree with anything confirmed still fail.
