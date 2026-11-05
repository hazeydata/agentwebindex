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

## newsfetch (method (a), from news_2026-46)

`python -m awibench.newsfetch --latest` (the weekly news-set job runs this after newsgen). Weeks ≤ 2026-45 do not fetch: each `url_exact` row's expected text is the feed headline, the set sha is the news file's sha, and the set records `expected_source: feed_title` (the task rows do not carry that key, so they stay byte-identical). Weeks ≥ 2026-46 fetch each article with plain HTTP (no provider call) and set the expected text to words that are on the visible page. Those rows record `expected_source: article_page`.

What is stored: the first non-empty `h1`. If there is no `h1`, `og:title`, and only when that string is also in the visible body. If that is not in the body, the `<title>` after a trailing ` - Site` or ` | Site` suffix is removed, and only when the trimmed string is in the visible body. Otherwise the row is excluded. Visible text is the body with `script`, `style`, `noscript`, `template` and the whole `head` removed. A string that appears only in `script` or `head` is not on the page.

Normalization: HTML entities are decoded, the string is Unicode NFC, and whitespace collapses to single spaces. Case, quotes and dashes are kept in the stored value. The stored value is capped at 300 characters on a word boundary (a word longer than 300 is cut at 300) and is a substring of the page text. At grade time the same entity decoding and NFC run, then `norm_text` casefolds and turns punctuation (quotes, dashes) into spaces.

Excluded rows are listed with `id` and a reason in `newsfetch_<week>.excluded.json`: `fetch_http_4xx` (404, 410, any other 4xx; no retry), `fetch_http_5xx` (500, 503, any other 5xx; one retry, then exclude), `timeout` (the plain fetch timed out; no retry), `paywall` (the page returned and a paywall marker is in the visible text, and no h1 / og:title / title candidate was on the page), `expected_text_not_on_page`, `fetch_error`. A paywall marker does not drop a row whose h1 is on the page. Emitted rows plus excluded rows equal the `url_exact` news rows. The derived `newsfetch_<week>.jsonl` is frozen (own sha). `newsfetch_<week>.meta.json` records the news file sha. Both shas are written on run log lines (`set_sha`, `news_sha`). A frozen file is not rebuilt, so a URL that starts answering later is not added back.

Redirects follow urllib, the same way `freeze.plain_fetch` does. The request itself must be http or https. If the final URL is on another domain (`util.domain`), the row records `final_url` and `domain_changed: true`.
