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
| runner.py | §4, D2, D4, AC11, AC12 | free-tier-only, $0 cap, `halted_quota`, `skipped_no_free_tier`, `skipped_tos`, `stale_auth_error` (any HTTP 401/403: run stops, its row is not published and the previous `latest.json` row is kept; every billed call stays in the ledger, 401 = 0 units by assumption, 403 = estimate), `skipped_all_errors`, line status `auth_error` (not scored); table rows carry `partial` (fewer set items reached than `set_rows`; only pass/fail/skipped_tos lines count) |
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
