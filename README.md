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
- `collectors/snapshot_prices.py` daily pricing-page snapshot + diff → `data/prices/<id>.json`, `data/prices/history/<id>.jsonl`, `data/prices/index.json`
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
SCRAPINGBEE_API_KEY, BROWSERBASE_API_KEY, STEEL_API_KEY, BROWSERLESS_API_KEY.
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
- Sets live in `bench/sets/` (not authored yet, so the validator fails and the live harness makes no calls). `bench/pricing.yaml` and `bench/tos_matrix.yaml` are all `NOT CAPTURED`/null until verified.
- Live runs: free tier only, `MONTHLY_CAP_USD` is hard-wired to `0` (D4). The runner halts a provider before a call would exceed its free quota. Quota usage goes to `data/spend/YYYY-MM-quota.json` and logs to `bench/runs/YYYY-MM-DD/<provider>/<set>.jsonl`. Tables are built from those logs into `data/benchmarks/latest.json`.
- Cron (UTC): news set Mondays 10:00 (06:00 ET during EDT; change it to 11:00 for EST); full run Mondays 11:23; re-probes Mon 17:23 (+6 h), Tue 11:23 (+24 h) and Thu 11:23 (+72 h); smoke run daily 10:41.
- Tests: `cd bench && python -m pytest tests/test_bench.py` (mock only, pytest `tmp_path`); `python tests/dry_run.py` runs the full mock pipeline into /tmp.
