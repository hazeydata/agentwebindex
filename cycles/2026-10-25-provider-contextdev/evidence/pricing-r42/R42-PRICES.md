# AWI Spec R42: Apify and Context.dev list prices (primary sources)

- Request: AWI Spec R42, George Michael (implement). Prepared 2026-10-27 (America/Toronto).
- Method: public GETs only (curl, desktop Chrome UA). robots.txt read first on apify.com, www.context.dev and docs.context.dev; all three allow these paths. No signup, login, key, form or spend. No CHALLENGE pages were hit.
- Captures: raw HTML/JSON plus extracted text (`*.txt`) in this folder. `fetch-log.tsv` lists file, URL, time read (EDT = America/Toronto, UTC-4), HTTP status and bytes. Hashes are in `SHA256SUMS`.
- Quote format: the vendor pages render prices as separate text nodes. Quotes below are verbatim node text in page order, with ` | ` marking node boundaries. Nothing was reworded.

## 0. What AWI uses (local code, read only)

Source: shared clone `<local shared clone>`. I ran `git fetch` only and read `origin/main` at `716e2ed` (2026-10-27 01:14 EDT, merge of PR #43) with `git show`/`git grep`. I did not check anything out. Line numbers refer to `origin/main`.

| What | Value | Where (file:line) |
|---|---|---|
| Apify search Actor | `apify/rag-web-browser` (Actor id `3ox4R101TgZz67sLr`), Standby endpoint, `maxResults` 10, `outputFormats` markdown, config `rag-raw-http-t240` (raw-http, requestTimeoutSecs 240) | collectors/benchmark.py:48, :50, :62, :67; bench/config.yaml:31; bench/pricing.yaml:384 |
| Apify fetch ("browser") Actor | `apify/web-fetch` (Actor id `a6Sqhlnt4touIHTub`), Standby, `formats: ["text"]` | collectors/benchmark.py:82, :84, :85, :98; bench/config.yaml:37; bench/pricing.yaml:438, :463 |
| Apify plan | Free plan ("$5 to spend", no card, $5 hard limit, $4.00 meter stop) | data/spend/2026-10-quota.json:2; bench/pricing.yaml:395; SPEC-bench.md:104 |
| Context.dev | search and scrape, **free tier only**; search sends 10 results, no markdown or highlights; scrape sends a URL only | README.md:62; SPEC-bench.md:106; bench/awibench/guard.py:114-116; collectors/benchmark.py:186-196; bench/pricing.yaml:659-662; collectors/providers.json:147, :153 |

(Actor ids come from the public Actor records captured below, not from the repo.)

## 1. Apify platform plan: https://apify.com/pricing

- Read: 2026-10-27 02:40:21 EDT. Capture: `apify-pricing.html` / `.txt`.
- Verbatim (Free card, txt lines 96-103): "Free | $5 to spend in Apify Store or on your own Actors | $0.2 per compute unit | Start for free | No credit card required."
- Verbatim (compute table, txt lines 199-204): "Compute units (CU) | $0.2 / CU | $0.2 / CU | $0.16 / CU | $0.13 / CU"
- Verbatim (FAQ, txt line 398): "Most Actors in Apify Store use pay-per-event pricing. You pay the prices listed on the Actor's page for events such as returning a result. Pay-per-usage Actors and your own Actors bill platform usage instead, including compute units, storage, proxies, and data transfer. Some pay-per-event Actors charge both."
- Per-call / per-1K: not applicable to the plan itself. The plan is a $5/month credit that Actor events and platform usage draw down.

## 2. Apify Actor `apify/rag-web-browser` (AWI search)

- Store page: https://apify.com/apify/rag-web-browser, read 02:40:21 EDT (`apify-rag-web-browser.html`).
- Store pricing tab: https://apify.com/apify/rag-web-browser/pricing, read 02:40:22 EDT (`apify-rag-web-browser-pricing.html` / `.txt`).
- Public Actor record (vendor JSON, no key): https://api.apify.com/v2/acts/apify~rag-web-browser, read 02:40:23 EDT (`apify-api-acts-rag-web-browser.json`). `pricingModel` "PAY_PER_EVENT", in effect since `startedAt` 2026-10-02T00:00:00Z (2026-10-01 20:00 EDT).
- Verbatim headline (pricing txt line 90): "from $1.33 / 1,000 fetched pages"
- Verbatim, Free plan card (txt): "Free | up to | 2,192 | fetched pages | $0 | / month | $5 to spend in Apify Store or on your own Actors"
- Verbatim, event table (txt; columns Free | Starter | Scale | Business):
  - "Fetched page | Primary | $2.28 | / 1,000 | $1.90 | / 1,000 | $1.62 | / 1,000 | $1.33 | / 1,000"
  - "Search result | $3.48 | / 1,000 | $2.90 | / 1,000 | $2.47 | / 1,000 | $2.03 | / 1,000"
  - "Actor Start | Infrequent | $0.000225 | $0.000225 | $0.000225 | $0.000225"
  - "Platform usage | Compute units (CU) | $0.20 | / CU | $0.20 | / CU | $0.16 | / CU | $0.13 | / CU"
- Actor record event descriptions (verbatim): search "Returns a set of links to be fetched"; fetch "Returns content of a fetched page"; Actor Start "Charged when the Actor starts running. Number of events charged depends on Actor memory (one event per GB, minimum one event)."
- **Published per-1K figures (Free plan, AWI's plan):** search event $3.48 / 1,000; fetched page $2.28 / 1,000; plus Actor Start and platform usage (CU).
- Derived (event charges only, Free plan): one AWI call = 1 search event + up to 10 fetched-page events (maxResults 10).
  - Max per call = $3.48/1,000 + 10 × $2.28/1,000 = $0.00348 + $0.02280 = **$0.02628 per call** (derived) = **$26.28 per 1,000 calls** (derived).
  - This excludes Actor Start ($0.000225 per GB per start; how often a Standby run starts is not published) and platform usage at $0.20/CU (CU per call is not published). The **all-in per-call price is not_found**.
- Derived check: $5 / $0.00228 = 2,192.98, which matches the page's "up to 2,192 fetched pages".

## 3. Apify Actor `apify/web-fetch` (AWI fetch / "browser")

- Store page: https://apify.com/apify/web-fetch, read 02:40:22 EDT (`apify-web-fetch.html`).
- Store pricing tab: https://apify.com/apify/web-fetch/pricing, read 02:40:23 EDT (`apify-web-fetch-pricing.html` / `.txt`).
- Public Actor record: https://api.apify.com/v2/acts/apify~web-fetch, read 02:40:23 EDT (`apify-api-acts-web-fetch.json`). `pricingModel` "PAY_PER_EVENT"; current tiered entry since 2026-09-10.
- Verbatim headline (txt line 90): "from $1.00 / 1,000 fetches"
- Verbatim, Free card: "Free | up to | 3,333 | fetches | $0 | / month | $5 to spend in Apify Store or on your own Actors"
- Verbatim, event table (Free | Starter | Scale | Business):
  - "Fetch | Primary | $1.50 | / 1,000 | $1.50 | / 1,000 | $1.25 | / 1,000 | $1.00 | / 1,000"
  - "Actor Start | Infrequent | $0.00005 | $0.00005 | $0.00005 | $0.00005"
  - "Platform usage | Platform costs | Included | Included | Included | Included"
- Actor record: fetch event "Successful fetch". The record also has GOLD/PLATINUM/DIAMOND at $0.001 and SILVER at $0.00125.
- **Published per-1K figure (Free plan): $1.50 / 1,000 fetches**, i.e. $0.0015 per successful fetch (derived: 1.50/1,000). Platform costs are included.
- Derived, matching AWI's billing assumption of one Actor Start per call (collectors/benchmark.py:85): $0.0015 + $0.00005 = **$0.00155 per call** = **$1.55 per 1,000 calls** (derived). If the Standby Actor starts less often than once per call, the cost moves toward $1.50/1K.
- Derived check: $5 / $0.0015 = 3,333.3, which matches "up to 3,333 fetches".

## 4. Context.dev API calls

- Pricing page: https://www.context.dev/pricing, read 02:40:24 EDT (`contextdev-pricing.html` / `.txt`).
- Credits doc: https://docs.context.dev/account/credits, read 02:40:24 EDT (`contextdev-docs-credits.html` / `.txt`).
- Billing doc: https://docs.context.dev/account/billing, read 02:41:05 EDT (`contextdev-docs-billing.html` / `.txt`).
- Verbatim, plan cards (pricing txt lines 54-121):
  - "Start with 1,000 free credits per month, no credit card required."
  - "Free Tier | ... | $0 | /month | ... | Credits per month | 1,000 | No overage"
  - "Developer | For side projects, MVPs, and lightweight apps. | $20 | /month | ... | Credits per month | 10,000 | $3.00 | per | 1,000 | credits overage"
  - "Pro | ... | $90 | /month | ... | Credits per month | 100K | 500K | 1M | 2.5M | $2.00 | per | 1,000 | credits overage" (the 100K/500K/1M/2.5M values are a credit-volume selector on the card)
- Verbatim, FAQ (txt line 255): "Context.dev offers Developer ($20/mo), Pro ($90/mo), Growth ($300/mo), Scale ($500/mo), and Scale 2.5M ($950/mo), plus Enterprise for teams that need more than 2.5M credits a month"
- Verbatim, FAQ (txt line 276): "Paid plans can use Auto-Topup in 1K-credit blocks: Developer $3.00, Pro $2.00, Growth $1.50, Scale $1.00, and Scale 2.5M $0.75 for new subscriptions. Existing subscriptions keep their rates."
- Verbatim, credit table (pricing txt): "A standard scrape uses 1 credit." / "Scrape | From 1 credit per call" / "Search Web | From 1 credit per call"
- Verbatim, credits doc (txt lines 84-91): "Scrape | 1 base; 2 with browser actions." / "Search | 1 per 10 returned results, rounded up. Highlights add 1 per 10 results when at least one result returns them. Markdown adds 1 per result returned with Markdown."
- Verbatim, billing doc plan table: "Free | 1,000/month" / "Developer | 10,000/month" / "Pro | 100,000/month" / "Growth | 500,000/month" / "Scale | 1,000,000/month" / "Scale 2.5M | 2,500,000/month"
- **Per call for AWI's requests:** search with 10 results and no markdown or highlights = 1 credit; plain scrape = 1 credit (credits doc). So 1 AWI call = 1 credit and 1,000 calls = 1,000 credits.
- **Per-1K figures:**
  - Free: $0, 1,000 credits/month, "No overage". That is 1,000 AWI calls per month at $0, then capped.
  - Developer overage / Auto-Topup: **$3.00 per 1,000 credits**, published. That is $3.00 per 1,000 AWI calls, or $0.003 per call (derived: 3.00/1,000).
  - Developer included credits (derived): $20 / 10,000 credits = $0.002 per credit = **$2.00 per 1,000 calls** if all 10,000 are used.
  - Pro overage: $2.00 per 1,000 credits, published. Pro included (derived): $90 / 100,000 = $0.90 per 1,000 calls.
- Change since AWI's 2026-10-25 read (bench/pricing.yaml:696): the three-way conflict recorded there ($20 card vs "$19" FAQ; overage $3.00 vs Auto-Topup $2.20; billing doc "7,500/month") **no longer appears** on today's pages. The card, FAQ and billing doc now all say Developer $20/mo, 10,000 credits, $3.00 per 1K. Existing subscriptions may keep older rates ("Existing subscriptions keep their rates").

## Summary table

| Item | Vendor URL | Published price (verbatim) | Per call / per 1K |
|---|---|---|---|
| Apify Free plan | https://apify.com/pricing | "$5 to spend in Apify Store or on your own Actors \| $0.2 per compute unit \| No credit card required." | n/a (monthly credit pool) |
| apify/rag-web-browser search event | https://apify.com/apify/rag-web-browser/pricing | "Search result \| $3.48 \| / 1,000" (Free) | $3.48 / 1K searches (published) |
| apify/rag-web-browser fetched page | https://apify.com/apify/rag-web-browser/pricing | "Fetched page \| Primary \| $2.28 \| / 1,000" (Free) | $2.28 / 1K pages (published); AWI call max $26.28 / 1K calls, events only (derived) + Actor Start + CU |
| apify/web-fetch | https://apify.com/apify/web-fetch/pricing | "Fetch \| Primary \| $1.50 \| / 1,000" (Free) + "Actor Start \| $0.00005" | $1.50 / 1K (published); $1.55 / 1K calls with one start per call (derived) |
| Context.dev Free | https://www.context.dev/pricing | "$0 \| /month \| Credits per month \| 1,000 \| No overage" | 1,000 calls/month at $0 (1 credit per AWI call) |
| Context.dev Developer | https://www.context.dev/pricing | "$20 \| /month \| Credits per month \| 10,000 \| $3.00 \| per \| 1,000 \| credits overage" | $3.00 / 1K calls overage (published); $2.00 / 1K included (derived) |
| Context.dev credit rule | https://docs.context.dev/account/credits | "Search \| 1 per 10 returned results, rounded up." / "Scrape \| 1 base; 2 with browser actions." | 1 credit per AWI search (10 results) or scrape |

## Gaps

1. **not_found:** All-in per-call price for `apify/rag-web-browser`. The page lists platform usage at "$0.20 / CU" but does not publish CU per call. Actor Start is billed "one event per GB, minimum one event", and no page says how often a Standby run starts.
2. **not_found:** Actor Start frequency for `apify/web-fetch` in Standby mode. That is why the per-call figure is given as a range: $1.50 to $1.55 per 1K, derived.
3. Code drift, for implement: collectors/benchmark.py:51 (`APIFY_FREE_EVENT_USD`) bills RAG calls on search and fetch events only. The live Actor record also lists an Actor Start event at $0.000225 (in effect since 2026-10-02) plus platform usage. The repo's own meter-based estimate of 4.08 cents per call (bench/pricing.yaml:389) already reflects this.
4. Docs drift, for implement: the credits doc now says search "Markdown adds 1 per result returned with Markdown", but bench/awibench/guard.py:119/125 assumes "+ceil(n/10) with markdown". AWI's request sends no markdown (guard.py:116), so it costs nothing today. It would matter if markdown were turned on.
5. Context.dev prices are the public self-serve list. Per the billing doc, "Legacy and custom plans can differ; the dashboard shows your organization's entitlement." AWI's org entitlement was not checked: no login.
6. Context.dev Growth, Scale and Scale 2.5M included-credit costs come only from the FAQ prices plus the billing-doc allowances. Those plans have no cards of their own on the pricing page. I did not derive figures for them.
7. Prices on Apify paid plans (Starter/Scale/Business) are listed above for reference. AWI is on Free, so the Free column is the one that applies.

## Link check

`python3 <local> ... --out-dir linkcheck-2026-10-27` run at 2026-10-27 02:42 EDT: 10 unique URLs, OK 10, DEAD 0, CHALLENGE 0, REDIRECT 0, ERROR 0, BLOCKED 0, ROBOTS-SKIPPED 0, AUTH-REQUIRED 0. Report: `linkcheck-2026-10-27/linkcheck-report.md`.
