# Benchmark sets (SPEC-bench §1)
Not authored yet. Expected files: `search_evergreen.jsonl` (100: facts/reference 30, docs/API lookup 25, local/entity 10, academic 10, long-tail 15, non-English 10), `scrape_urls.jsonl` (150), `browser_tasks.yaml` (20),
plus weekly `news_YYYY-WW.jsonl` (up to 50 rows; under 25 is published as "short week (n=X)", with a `.meta.json` count) from `python -m awibench.newsgen`. Validate with `python -m awibench.validate`.
Rows are authored and reviewed by people (fact desk), then frozen with `awibench.freeze` (AC2). Never fill these with synthetic rows:
synthetic fixtures live only in `bench/tests/fixtures/`.
