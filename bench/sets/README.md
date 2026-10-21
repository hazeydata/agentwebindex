# Benchmark sets (SPEC-bench §1)
Authored and reviewed by the fact desk, frozen with `awibench.freeze` (AC2). Never fill these with synthetic rows:
synthetic fixtures live only in `bench/tests/fixtures/`.

| File | Rows | sha256 |
|---|---|---|
| `search_evergreen.jsonl` | 100 (facts/reference 30, docs/API lookup 25, local/entity 10, academic 10, long-tail 15, non-English 10) | `6b488a955e0896d91fd0db99b1936c38721f79e68c02cef0589e70664874a527` |
| `scrape_urls.jsonl` | 150 | `118f46210f3c65135b8611319c8d91afe35fb9554f26469b0c1abf8baf2a3c1a` |
| `browser_tasks.yaml` | 20 | `e78ac14e99ae3da36c820eedeb02dad3066db05025c06ba9646969e32c43ed1e` |

Freeze manifest: `freeze-2026-10-20-thirdpass.json` (third pass, 0 failures: 100 source URLs, 150 markers). The committed files match its sha256 values.
Earlier logs `freeze-2026-10-20.json` (8 failures) and `freeze-2026-10-20-refreeze.json` (3 failures) are kept for history; they describe superseded versions of `scrape_urls.jsonl`/`search_evergreen.jsonl`.

Weekly `news_YYYY-WW.jsonl` (up to 50 rows; under 50 is published as "short week (n=X)", under 25 also as "below minimum (n=X)", with a `.meta.json` count) come from `python -m awibench.newsgen`.
Validate with `python -m awibench.validate`.
