# bench/ (SPEC-bench v1)
Stdlib-only Python package `awibench`. Run commands from `site/bench/`.

| Module | Spec | Command |
|---|---|---|
| validate.py | §1, AC1, AC4 | `python -m awibench.validate [SETS_DIR]` (exit 1 on any error) |
| freeze.py | AC2 | `freeze()` with injected status-fetcher + reference browser; `retire()` logs retirements |
| newsgen.py | §1b, D1, AC3, AC13 | `python -m awibench.newsgen --date 2026-10-19T06:00:00-04:00 --seed 0 --fixtures tests/fixtures/rss --out /tmp/x.jsonl` |
| checks.py | §1 pass rules | search / news / scrape (marker, min_chars, challenge list) / browser |
| cost.py | §2, D3, AC5, AC6, AC14 | entry + volume tier columns, per 1k and per 1k successful |
| freshness.py | §3, AC7 | fresh_hit@10, median_lag_h, censored_share, `misses_due()` for re-probes |
| runner.py | §4, D2, D4, AC11, AC12 | free-tier-only, $0 cap, `skipped_no_free_tier`, `skipped_tos`, and `stale_<reason>` with reason `quota`, `timeout` (config `run_budget_s`), `auth_error` (any HTTP 401/403) or `error` (every call errored, or an incomplete set). CEO ruling 2026-10-19: a row publishes only when the run covered the full set; a stale provider keeps its previous `latest.json` row in tables and freshness, and with no previous row it is left off the site (not in tables, not_run_yet or skips). Billed spend is always logged. CEO billing ruling 2026-10-19 (status-line rule): once an HTTP status line has arrived the call bills the estimate (including 403, 429, 5xx, a 200 with error text, and a mid-body cut-off); a call with no status line is logged as `unbilled_no_response`. It bills 0 only when no status line arrived (connection, DNS or TLS handshake failure, timeout before the status line, blocked network), or on HTTP 401 (ASSUMPTION, accepted by the CEO). Line statuses `auth_error` and `unbilled_no_response` are not scored |
| tables.py | §4, AC8 | `python -m awibench.tables RUNS_DIR OUT.json`; refuses untraced cells and mock output into published dirs |
| secrets_scan.py | AC10 | `python -m awibench.secrets_scan` |

Config: `config.yaml` (feeds, caps, challenge signatures, demo-site allowlist, CEO free-tier list), `pricing.yaml`, `tos_matrix.yaml`.
All are JSON-compatible YAML so no PyYAML is needed. Every pricing/ToS value is `NOT CAPTURED`/null until read from the vendor.

Tests (MOCK only, temp dirs): `python -m unittest -v tests.test_bench`. Fixtures: `tests/fixtures/rss/*.xml` (from `tests/make_rss_fixtures.py`), `tests/mocks.py`.
Mock dry run of the full pipeline: `python tests/dry_run.py` (writes to /tmp only).

## Adapter contract (collectors/benchmark.py `ADAPTERS`)
`fn(key, item) -> dict`, plus optional `billed_units` (the provider's billing unit; browser = raw minutes), `proxy_gb` and `http_status`.
- search: `{"results": [{"url","title","snippet","published_at"?}, ...]}`
- scrape: `{"text", "html"?}`
- browser: `{"final_url"?, "extracted_value"?, "dom_text"?, "file_bytes"?}`

Marker `selector_text` value format: `"<selector>::<text>"`, with selector = `tag`, `#id`, `.class` or `tag.class`.

**Stale runs still write their run log.** A `stale_<reason>` run's lines go to `bench/runs/YYYY-MM-DD/<provider>/<set>.jsonl` stamped `run_status: stale_<reason>` (spend audit); `tables.collect` never publishes a stamped or incomplete log. Only a provider with no data call and nothing billed writes no lines.
