# Decisions: AWI-PROVIDER-KEENABLE-001 (2026-10-26)

## Publishing permission (AC17)
Fred's 7:35 AM ruling, as the CEO relayed it:
> "Yes, the CEO's X post is enough permission to publish Keenable."

The terms clauses it covers, from https://keenable.ai/terms (Date of Last Revision: October 3, 2026):
> "provide to any third party the results of any benchmark tests of the Service."
>
> "distribute or otherwise disclose the API Output to any third parties on a standalone basis."

Written permission: the @styskin post https://x.com/styskin/status/2114208595525144970 (2026-10-25 00:13:03 EDT / 04:13:03 UTC):
> "@HazeyDataFred Please include @KeenableAI into your tests."

The post couldn't be archived (archive.org save returned HTTP 523; no snapshot). The saved public X API read is the evidence: `evidence/posts-api.json.txt` (copied from `/workspace/agentwebindex-evidence/vendor-x/posts-api.json.txt`).

## Open questions closed
- **OQ-K4, closed (CEO):** scores and metrics only. No raw Keenable result URLs or snippets in any public output (AC23; config `publish_raw_results: false`; run-log lines keep `results_count` and the response hash).
- **OQ-K5, closed (CEO ruling):** the terms' competitor clause ("No employee, independent contractor, agent, or affiliate of any competing company developing and/or providing an AI-powered web index ...") does not apply: HazeyData benchmarks indexes; it doesn't build one.
- **OQ-K2 (free tier only):** `paid_usage_allowed: false`. HTTP 402, a payment-required error, a usage block or billed units > 0 stops all keenable calls as `stopped_paid_usage` (R1/AC20).

## Fact-check (AC10): confirmed
The fact desk (Lucille) confirmed the post, the identity and the officer role from a primary source: Andrey Styskin is Co-founder & CEO per the keenable.ai team section ("Andrey Styskin    Co-founder & CEO    ex-CEO of Search and Ads at Yandex"; JSON-LD founder jobTitle "Co-founder and CEO", sameAs https://x.com/styskin).
- Fact-check file: `/workspace/agentwebindex-evidence/vendor-x/fact-check-lucille.md`
- keenable.ai archive: https://web.archive.org/web/20261026113537/https://keenable.ai/
- Residual risk (from the fact-check): the role rests on the vendor's own site; blue verification doesn't vet identity; the post has no third-party archive; it is a solicited reply in HazeyData's own thread.

## Phasing
- **K1** (hazeydata/agentwebindex#29): adapter, guards, config and tos entry, all default-closed; the vendor_confirmation record stays null in K1; mocks only.
- **K2 and K3 waited on open questions** (OQ-K4, OQ-K5, the AC10 fact-check, and the endpoint and free-tier limits). They are now closed, so the K2 flip PR (hazeydata/agentwebindex#30) records the vendor_confirmation and sets `live_run_allowed: true`; `paid_usage_allowed` stays false.
- Endpoint: keyless `POST https://api.keenable.ai/v1/search/public` with `X-Keenable-Title: AgentWebIndex` (docs.keenable.ai). Serial, at most 9/s, under 900 per rolling hour; a 429 gets at most one retry after Retry-After, then `rate_limited`; a 402 stops all calls.
- Pricing: pricing.yaml records $4 per 1,000 requests (pay as you go, https://keenable.ai/pricing) outside `providers`; Keenable stays off Also priced and Prices this cycle (tested).

## Later (not this cycle)
- Styskin's reproduced eval, https://x.com/styskin/status/2114405816669544464: Exa needs `contents.highlights=true`, and the exact-URL judge is Google-biased. Methodology notes for a later spec; not implemented.
- K3: the committed tos entry still says `benchmarking: forbidden`, so the runner records `excluded_tos_benchmarking` even after the flip. Updating that clause per the OQ-K5 ruling is a separate, non-flip PR (it can't ride in the flip PR).
- K3: `collectors/benchmark.py keyed_providers()` needs a key env, so keenable is never picked for a live run; runner eligibility needs a pricing free_tier entry.
