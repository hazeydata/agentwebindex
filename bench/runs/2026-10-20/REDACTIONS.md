# Redactions in the 2026-10-20 run logs

Applied after the run, before publication. Only third-party result URLs were changed; no set item, status or score field was edited.
The re-grade of latest.json from these logs gives the same scores as before redaction.

| File | Line | Field | Masked form | Reason |
|---|---|---|---|---|
| apify/news_2026-43.jsonl | 21 | results[8].url | `https://noozlet.com/c/redacted-email/p/216/...` | an email address was embedded in the result URL path |
| tavily/news_2026-43.jsonl | 21 | results[4].url | `https://noozlet.com/c/redacted-email/p/216/...` | same URL as above, email address embedded in the path |
| exa/news_2026-43.jsonl | 33 | results[8].url | `https://redacted-host.org/news/evacuation-orders-...` | host name matched a pre-push scan banned-substring rule (false positive); path left as returned |
