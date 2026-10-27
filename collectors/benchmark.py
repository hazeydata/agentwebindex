#!/usr/bin/env python3
"""Live benchmark entry point (GitHub Actions). Wires SPEC-bench (bench/awibench) into the hooks.

- load_test_set  -> bench/sets/* (validated; refuses to run on invalid sets)
- cost_per_1k    -> awibench.cost.cost_columns (entry + volume tier, D3)
- freshness_score-> awibench.freshness.freshness (news sets)
- run log        -> bench/runs/YYYY-MM-DD/<provider>/<set>.jsonl -> awibench.tables -> data/benchmarks/latest.json
Free tier only, hard $0 cap (D4). Providers without a key, adapter, known free quota or ToS clearance are skipped.
Search adapters only (run 1); there is no mock path here (mocks live in bench/tests).
"""
import argparse, datetime as dt, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT / "bench"))
from awibench import runner, tables, validate, cost, freshness, newsfetch, guard, gates
from awibench.util import load_yaml, read_jsonl, sha256_file

BENCH = ROOT / "bench"
def _body(r):
    try: return r.read()
    except Exception as e: raise RuntimeError(f"HTTP {r.status}: body cut off: {type(e).__name__}") from None

def _post(url, headers, body):
    import urllib.request, urllib.error
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "AgentWebIndexBot/1.0"} | headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return r.status, json.loads(_body(r))
    except urllib.error.HTTPError as e:   # message carries status + short vendor text only, never the key
        ra = (e.headers or {}).get("Retry-After") if e.code == 429 else None   # AC6: the runner waits this long (bounded)
        raise RuntimeError(f"HTTP {e.code}" + (f" (Retry-After: {ra})" if ra else "") + f": {e.read()[:200].decode('utf-8', 'replace')}") from None

def _exa_search(key, item):
    st, d = _post("https://api.exa.ai/search", {"x-api-key": key}, {"query": item["query"], "numResults": 10, "type": "auto"})
    return {"http_status": st, "billed_units": 1, "results": [{"url": r.get("url"), "title": r.get("title"), "snippet": None,
            "published_at": r.get("publishedDate")} for r in d.get("results", [])]}

def _tavily_search(key, item):   # basic depth; billed units left to the runner's 2-credit guard (unverified)
    st, d = _post("https://api.tavily.com/search", {"Authorization": f"Bearer {key}"}, {"query": item["query"], "max_results": 10, "search_depth": "basic"})
    return {"http_status": st, "results": [{"url": r.get("url"), "title": r.get("title"), "snippet": (r.get("content") or "")[:300],
            "published_at": r.get("published_date")} for r in d.get("results", [])]}

def _firecrawl_search(key, item):   # pricing.yaml credits_per_call search = 2
    st, d = _post("https://api.firecrawl.dev/v1/search", {"Authorization": f"Bearer {key}"}, {"query": item["query"], "limit": 10})
    if d.get("success") is False: raise RuntimeError(f"firecrawl error: {str(d.get('error'))[:200]}")
    return {"http_status": st, "results": [{"url": r.get("url"), "title": r.get("title"), "snippet": r.get("description"),
            "published_at": None} for r in d.get("data", [])]}

# Apify RAG Web Browser (Store Actor apify/rag-web-browser), Standby endpoint: Google search + page fetch, priced per event.
# Event prices at the Apify Free plan (USD), spec R42.1 source read 2026-10-27 02:40 ET from https://apify.com/apify/rag-web-browser/pricing
# and the public Actor record: search 0.00348, fetched page 0.00228, Actor Start 0.000225 (in effect since 2026-10-02). Actor Start is
# "one event per GB, minimum one event", and the captured Actor record publishes memoryMbytes 8192 (defaultRunOptions and actorStandby),
# so one start = 8 events = $0.0018 (APIFY_RAG_START_EVENTS). How often a Standby run starts is not published (runs are reused: desired 10,
# max 14 requests per run), so AWI counts one start per call; a hedge, not a minimum or a maximum. Platform usage ($0.20/CU) is also
# billed, but CU per call is not published, so it is not in billed_units (the unit count is low; the unit price, 1 unit = 1 cent, is exact;
# the guard's 4.08-cent per-call estimate in bench/pricing.yaml comes from the live meter and covers it).
APIFY_RAG_URL = "https://rag-web-browser.apify.actor/search"
APIFY_FREE_EVENT_USD = {"search": 0.00348, "fetch": 0.00228, "start": 0.000225}
APIFY_RAG_START_EVENTS = 8   # 8192 MB / 1 GB per event (Actor record memoryMbytes 8192)

def _get_json(url, headers, timeout=150):
    import urllib.request, urllib.error
    req = urllib.request.Request(url, headers={"User-Agent": "AgentWebIndexBot/1.0"} | headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.status, json.loads(_body(r))
    except urllib.error.HTTPError as e:
        ra = (e.headers or {}).get("Retry-After") if e.code == 429 else None   # AC6: the runner waits this long (bounded)
        raise RuntimeError(f"HTTP {e.code}" + (f" (Retry-After: {ra})" if ra else "") + f": {e.read()[:200].decode('utf-8', 'replace')}") from None

def _apify_search(key, item):
    """Ranked results from the Actor's searchResult.rank; result URL = the fetched page's metadata.url (Google returns
    redirect links in searchResult.url). billed_units = US cents charged at Free-plan event prices (one Actor Start of 8 events + 1 search + 1 per
    fetched page); platform usage (CU) is not published per call and not included."""
    import urllib.parse
    cfg = load_yaml(BENCH / "config.yaml"); name = cfg["apify_search_config"]; conf = cfg["apify_search_configs"][name]
    q = urllib.parse.urlencode({"query": item["query"], "maxResults": 10, "outputFormats": "markdown"} | conf)
    st, d = _get_json(f"{APIFY_RAG_URL}?{q}", {"Authorization": f"Bearer {key}"}, timeout=conf["requestTimeoutSecs"] + 60)
    if isinstance(d, dict) and (d.get("error") or d.get("code")):   # AC5: Apify error object in a 2xx body (e.g. payment-required)
        e = d.get("error"); code = d.get("code") or (e.get("type") if isinstance(e, dict) else e)
        raise RuntimeError(f"HTTP {st}: rag-web-browser error ({str(code)[:60]})")
    d = sorted(d if isinstance(d, list) else [], key=lambda x: (x.get("searchResult") or {}).get("rank") or 99)
    res = []
    for x in d:
        md, sr = x.get("metadata") or {}, x.get("searchResult") or {}
        res.append({"url": md.get("url") or md.get("canonicalUrl") or sr.get("url"), "title": sr.get("title") or md.get("title"),
                    "snippet": sr.get("description"), "published_at": None})
    fetched = sum(1 for x in d if (x.get("crawl") or {}).get("requestStatus") == "handled")
    cents = 100 * (APIFY_RAG_START_EVENTS * APIFY_FREE_EVENT_USD["start"] + APIFY_FREE_EVENT_USD["search"] + fetched * APIFY_FREE_EVENT_USD["fetch"])
    return {"http_status": st, "billed_units": round(cents, 4), "fetched_pages": fetched, "results": res, "config": name}

# Apify Web Fetch (Store Actor apify/web-fetch), Standby endpoint: one URL -> page text. Pay per event, no platform usage.
# FREE-plan event prices from the Actor's public pricing record, read 2026-10-26 (USD): fetch 0.0015 (successful only), Actor Start 0.00005.
APIFY_WEB_FETCH_URL = "https://web-fetch.apify.actor/"
APIFY_WEB_FETCH_EVENT_USD = {"fetch": 0.0015, "start": 0.00005}
APIFY_WEB_FETCH_CLIENT_TIMEOUT_S = 150   # the Actor's own fetch timeout is 2 min (FETCH_TIMEOUT)

def _post_json(url, headers, body, timeout):
    import urllib.request, urllib.error
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "AgentWebIndexBot/1.0"} | headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.status, json.loads(_body(r))
    except urllib.error.HTTPError as e:   # status + short vendor text only, never the key
        ra = (e.headers or {}).get("Retry-After") if e.code == 429 else None   # AC6: the runner waits this long (bounded)
        raise RuntimeError(f"HTTP {e.code}" + (f" (Retry-After: {ra})" if ra else "") + f": {e.read()[:200].decode('utf-8', 'replace')}") from None

def _apify_web_fetch(key, item):
    """browser task: POST {url, formats: [text]} -> dom_text = the Actor's text, final_url = fetch.loadedUrl.
    billed_units = US cents at FREE event prices (Actor Start + one fetch event; the Actor charges fetch only when it returns content)."""
    st, d = _post_json(APIFY_WEB_FETCH_URL, {"Authorization": f"Bearer {key}"}, {"url": item["start_url"], "formats": ["text"]},
                       timeout=APIFY_WEB_FETCH_CLIENT_TIMEOUT_S)
    if not isinstance(d, dict) or d.get("code") or d.get("text") is None:
        code = d.get("code") if isinstance(d, dict) else "non-object body"
        raise RuntimeError(f"HTTP {st}: web-fetch returned no text ({str(code)[:60]})")
    f = d.get("fetch") or {}
    cents = 100 * (APIFY_WEB_FETCH_EVENT_USD["start"] + APIFY_WEB_FETCH_EVENT_USD["fetch"])
    return {"http_status": st, "billed_units": round(cents, 4), "dom_text": d["text"], "final_url": f.get("loadedUrl"),
            "target_status": f.get("httpStatusCode"), "config": "web-fetch-text"}

def apify_meter(key):
    st, d = _get_json("https://api.apify.com/v2/users/me/limits", {"Authorization": f"Bearer {key}"}, timeout=30)
    return d["data"]["current"]["monthlyUsageUsd"]

BROWSERLESS_CONTENT_URL = "https://production-sfo.browserless.io/content"

def _html_text(h):
    from html.parser import HTMLParser
    class P(HTMLParser):
        def __init__(s): super().__init__(convert_charrefs=True); s.buf = []; s.skip = 0
        def handle_starttag(s, t, a): s.skip += t in ("script", "style", "noscript")
        def handle_endtag(s, t): s.skip -= t in ("script", "style", "noscript") and s.skip > 0
        def handle_data(s, d):
            if not s.skip: s.buf.append(d)
    p = P(); p.feed(h); return " ".join(p.buf)

def _browserless_fetch(key, item):
    """Browserless /content (REST): rendered HTML of start_url. billed_units = wall-clock minutes; the runner converts
    them to Browserless units (30 s per connection, config browser_unit_seconds)."""
    import time, urllib.request, urllib.error
    body = {"url": item["start_url"], "gotoOptions": {"waitUntil": "domcontentloaded", "timeout": int(item.get("timeout_s", 30)) * 1000 - 5000}}
    req = urllib.request.Request(f"{BROWSERLESS_CONTENT_URL}?token={key}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "AgentWebIndexBot/1.0"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=int(item.get("timeout_s", 30)) + 30) as r: st, h = r.status, _body(r).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:   # never echo the request URL (it carries the token)
        ra = (e.headers or {}).get("Retry-After") if e.code == 429 else None   # AC6: the runner waits this long (bounded)
        raise RuntimeError(f"HTTP {e.code}" + (f" (Retry-After: {ra})" if ra else "") + f": {e.read()[:200].decode('utf-8', 'replace')}") from None
    except Exception as e:
        raise RuntimeError(f"{type(e).__name__}: {str(e).replace(key, '***')[:200]}") from None
    return {"http_status": st, "billed_units": (time.perf_counter() - t0) / 60, "dom_text": _html_text(h), "html_chars": len(h)}

def _browserless_scrape(key, item):
    """scrape_urls through Browserless /content: rendered HTML of item url. billed_units = Browserless units (30 s blocks)."""
    import math
    out = _browserless_fetch(key, {"start_url": item["url"], "timeout_s": 30})
    sec = out["billed_units"] * 60
    return {"http_status": out["http_status"], "billed_units": max(1, math.ceil(sec / 30 - 1e-9)), "text": out["dom_text"], "html_chars": out["html_chars"]}

BRIGHTDATA_REQUEST_URL = "https://api.brightdata.com/request"

def _brightdata_scrape(key, item):
    import urllib.request, urllib.error
    zone = os.environ.get("BRIGHTDATA_ZONE")
    if not zone: return {"error": "BRIGHTDATA_ZONE not set; no request made", "billed_units": 0}
    req = urllib.request.Request(BRIGHTDATA_REQUEST_URL, data=json.dumps({"zone": zone, "url": item["url"], "format": "raw"}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "AgentWebIndexBot/1.0", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r: st, h = r.status, _body(r).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        ra = (e.headers or {}).get("Retry-After") if e.code == 429 else None   # AC6: the runner waits this long (bounded)
        raise RuntimeError(f"HTTP {e.code}" + (f" (Retry-After: {ra})" if ra else "") + f": {e.read()[:200].decode('utf-8', 'replace')}") from None
    return {"http_status": st, "billed_units": 1, "text": _html_text(h), "html": h}

# context.dev (AWI-PROVIDER-CONTEXTDEV-001). Bearer auth only; the key is never put in a URL, a log line or an error text.
# Endpoint paths and request/response field names are from docs.context.dev and are UNVERIFIED (never called live: R9).
CONTEXTDEV_SEARCH_URL = "https://api.context.dev/v1/web/search"   # unverified; never called live (R9)
CONTEXTDEV_SCRAPE_URL = "https://api.context.dev/v1/web/scrape"   # unverified; never called live (R9)
CONTEXTDEV_USAGE_URL = "https://api.context.dev/v1/org/usage"     # docs.context.dev/api-reference/usage/retrieve (GET, 0 credits, needs logs:read)

def _contextdev_post(key, url, body):
    import urllib.request, urllib.error
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "AgentWebIndexBot/1.0", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return r.status, json.loads(_body(r))
    except urllib.error.HTTPError as e:   # status + Retry-After + short vendor text only; any echo of the key is scrubbed
        ra = (e.headers or {}).get("Retry-After") if e.code == 429 else None
        raise RuntimeError(f"HTTP {e.code}: {f'Retry-After: {ra}; ' if ra else ''}{e.read()[:200].decode('utf-8', 'replace').replace(key, '***')}") from None
    except RuntimeError as e:   # _body: "HTTP 200: body cut off"
        raise RuntimeError(str(e).replace(key, "***")[:200]) from None
    except Exception as e:
        raise RuntimeError(f"{type(e).__name__}: {str(e).replace(key, '***')[:200]}") from None

def _contextdev_search(key, item):   # request options come from guard.contextdev_request, the same source as the runner's pre-call credit estimate
    o = guard.contextdev_request("search", item)
    st, d = _contextdev_post(key, CONTEXTDEV_SEARCH_URL, {"query": item["query"], "maxResults": o["max_results"]} | {k: True for k in ("markdown", "highlights") if o.get(k)})
    rows = d.get("results") or d.get("data") or []
    return {"http_status": st, "billed_units": guard.contextdev_credits("search", o, len(rows)), "results": [{"url": r.get("url"), "title": r.get("title"),
            "snippet": r.get("snippet") or r.get("description"), "published_at": r.get("published_at") or r.get("publishedDate")} for r in rows]}

def _contextdev_scrape(key, item):
    st, d = _contextdev_post(key, CONTEXTDEV_SCRAPE_URL, {"url": item["url"]})
    html = d.get("html") or ""
    return {"http_status": st, "billed_units": guard.contextdev_credits("scrape", guard.contextdev_request("scrape", item), None),
            "text": d.get("markdown") or d.get("text") or d.get("content") or _html_text(html), "html": html}

def contextdev_meter(key):
    """Read-only usage meter: GET /v1/org/usage (0 credits). Returns credits used in the current period, or None on any error
    (no period, bad shape, HTTP error, no response). GET only; the key goes in the Authorization header and is never echoed."""
    try:
        st, d = _get_json(CONTEXTDEV_USAGE_URL, {"Authorization": f"Bearer {key}"}, timeout=30)
        used = (d.get("period") or {}).get("credits_used")
        return used if type(used) is int and used >= 0 else None
    except Exception: return None

# Keenable (AWI-PROVIDER-KEENABLE-001 AC18/AC22). Keyless public endpoint, the ONLY URL this adapter calls (docs.keenable.ai/api-reference/search:
# "`POST /v1/search/public` takes the same body and returns the same shape, with no API key"; X-Keenable-Title is required). No key, no
# signup; the keyed /v1/search and the x402 routes are never called, and no keenable.ai page is fetched. Built with mocks in K1; open since the K2 flip PR #36 (keyless, so
# any harness run calls it; scheduled runs are stopped only by the benchmark workflow being disabled_manually in the repo's Actions
# settings; its re-enable within the caps is approved (spec R40), sequenced after paper desk R25 reaches Kitty).
# No live keenable call has been made yet as of this comment. Live runs are covered by Fred's Oct 26 go within spend caps (R24, R39); there is no per-run go.
KEENABLE_SEARCH_URL = "https://api.keenable.ai/v1/search/public"
KEENABLE_TITLE = "AgentWebIndex"

def _keenable_search(key, item):   # `key` is unused (keyless); the runner passes a placeholder
    import urllib.request, urllib.error
    req = urllib.request.Request(KEENABLE_SEARCH_URL, data=json.dumps({"query": item["query"]}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "AgentWebIndexBot/1.0", "X-Keenable-Title": KEENABLE_TITLE})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: st, d = r.status, json.loads(_body(r))
    except urllib.error.HTTPError as e:   # status + Retry-After only (AC23: no vendor text that could carry results)
        ra = (e.headers or {}).get("Retry-After") if e.code == 429 else None
        raise RuntimeError(f"HTTP {e.code}: {f'Retry-After: {ra}; ' if ra else ''}keenable error") from None
    out = {"http_status": st, "billed_units": 0, "results": [{"url": r.get("url"), "title": r.get("title"), "snippet": r.get("snippet") or r.get("description"),
           "published_at": r.get("published_at")} for r in (d.get("results") or [])]}
    if d.get("keenable/usage") is not None or d.get("usage") is not None: out["usage"] = d.get("keenable/usage") or d.get("usage")   # metered: guard.paid_stop stops
    return out

ADAPTERS = {"exa": {"search": _exa_search}, "tavily": {"search": _tavily_search}, "firecrawl": {"search": _firecrawl_search},
            "apify": {"search": _apify_search, "browser": _apify_web_fetch}, "browserless": {"browser": _browserless_fetch, "scrape": _browserless_scrape},
            "brightdata": {"scrape": _brightdata_scrape}, "contextdev": {"search": _contextdev_search, "scrape": _contextdev_scrape},
            "keenable": {"search": _keenable_search}}
# {provider_id: {"search"|"scrape"|"browser": fn(key, item) -> dict}}; see bench/README.md. Scrape/browser adapters wait for their sets.

def keyed_providers(provs, env, cfg=None, tos=None):
    """Providers that have a collector AND a non-empty key in env, plus (AC24) keyless providers (guard.KEYLESS), which need no
    env key and are gated only by guard.live_allowed(cfg, tos, pid). Empty -> the run is skipped (nothing written)."""
    def ok(p):
        if not ADAPTERS.get(p["id"]): return False
        if p["id"] in guard.KEYLESS: return cfg is not None and tos is not None and guard.live_allowed(cfg, tos, p["id"]) is True
        return bool(p.get("key_env") and env.get(p["key_env"]))
    return [p["id"] for p in provs if ok(p)]

def run_pricing(pricing_doc):
    """AC24: the runner's pricing map = pricing.yaml providers plus the keenable free_tier from keenable_price_record (run eligibility
    only; tables/Prices/Also priced keep reading pricing.yaml providers, so AC21 still holds). R41.1: the record's list price
    (usd_per_1k_requests) is the entry tier, so the pre-run projection has a price for keenable."""
    rec = pricing_doc.get("keenable_price_record") or {}; pr = dict(pricing_doc["providers"]); ft = rec.get("free_tier")
    if ft and "keenable" not in pr:
        pr["keenable"] = {"search": {"model": "per_request", "free_tier": ft}}
        if isinstance(rec.get("usd_per_1k_requests"), (int, float)):
            pr["keenable"]["search"]["tiers"] = {"entry": {"name": "list price (keenable_price_record)", "unit_price": rec["usd_per_1k_requests"] / 1000}}
    return pr

def keep_previous_rows(doc, prev_path, stale):
    """CEO ruling 2026-10-19: a stale_<reason> or stopped_<reason> provider (incomplete run) is not published. Its rows in tables and freshness
    are the previous latest.json rows, unchanged; with no previous row it has none. Returns the stale providers with a previous row."""
    if not stale: return set()
    prev = json.loads(Path(prev_path).read_text()) if Path(prev_path).exists() else {}
    for kind in doc["tables"]:
        doc["tables"][kind] = [r for r in doc["tables"][kind] if r["provider"] not in stale] + \
                              [r for r in (prev.get("tables") or {}).get(kind, []) if r["provider"] in stale]
    doc["freshness"] = [r for r in doc.get("freshness", []) if r["provider"] not in stale] + [r for r in prev.get("freshness", []) if r["provider"] in stale]
    doc["has_results"] = any(r["attempted"]["v"] for t in doc["tables"].values() for r in t)
    return {r["provider"] for t in doc["tables"].values() for r in t if r["provider"] in stale}

def is_stale(p): return p["status"].startswith(("stale_", "stopped_")) or p["status"] in guard.STOP_STATUSES   # keenable rate_limited
def stale_providers(s): return {p["provider"] for p in s["providers"] if is_stale(p)}

def fresh_data(s, held=()):
    """True if a provider that is not stale returned data. Data only from stale (or publish-held) providers -> latest.json not rewritten."""
    return any(p.get("data_calls") and not is_stale(p) and p["provider"] not in held for p in s["providers"])

def finish_doc(doc, s, cfg, prev_path, skips):
    """Previous rows for stale providers, not_run_yet and provider skips. A stale provider is never in not_run_yet or skips
    (no site badge); with no previous row it is left off the site entirely. Its run_summary reason says which case applied."""
    stale = stale_providers(s); kept = keep_previous_rows(doc, prev_path, stale)
    for p in s["providers"]:
        if is_stale(p) and not p.get("reason", "").endswith(("previous row kept", "not shown")):
            p["reason"] = p.get("reason", p["status"]) + ("; previous row kept" if p["provider"] in kept else "; no previous row, not shown")
    doc["not_run_yet"] = tables.not_run_yet(s, cfg, doc)
    for n in doc["not_run_yet"]:   # owner-set reasons (config not_run_yet_reasons) replace the bare status
        n["reason"] = (cfg.get("not_run_yet_reasons") or {}).get(n["provider"], n["reason"])
    for p in s["providers"]:
        if p["status"] != "ran" and not is_stale(p) and p["provider"] not in {x["provider"] for x in doc["not_run_yet"]}:
            skips.append({"scope": "provider", "provider": p["provider"], "category": p["category"], "status": p["status"], "reason": p.get("reason", p["status"])})
    return stale

def merge_previous(doc, s, prev_path, sets_seen):
    """Scoped runs (--only-sets/--only-providers) must not drop earlier entries (AWI-EVERGREEN-RUN-001 fix). Keeps previous
    latest.json skips and not_run_yet entries for providers/categories and sets this run did not touch; this run's entries win."""
    prev = json.loads(Path(prev_path).read_text()) if Path(prev_path).exists() else {}
    doc["skips"] = list(doc["skips"]); doc["not_run_yet"] = list(doc["not_run_yet"])   # copies: the run's own _skips log stays this run only
    touched = {(p["provider"], p["category"]) for p in s["providers"]}
    shown = {r["provider"] for t in doc["tables"].values() for r in t} | {p["provider"] for p in s["providers"]}
    def key(k): return ("set", k.get("set")) if k.get("scope") == "set" else (k.get("scope"), k.get("provider"), k.get("category"))
    have = {key(k) for k in doc["skips"]}
    for k in prev.get("skips", []):
        if key(k) in have: continue
        if k.get("scope") == "set" and k.get("set") in sets_seen: continue
        if k.get("scope") == "provider" and (k.get("provider"), k.get("category")) in touched: continue
        if k.get("scope") == "run": continue
        doc["skips"].append(k); have.add(key(k))
    names = {n["provider"] for n in doc["not_run_yet"]}
    for n in prev.get("not_run_yet", []):
        if n["provider"] not in names and n["provider"] not in shown: doc["not_run_yet"].append(n); names.add(n["provider"])

def load_test_set(name):
    f = BENCH / "sets" / (name if "." in name else f"{name}.jsonl")
    return load_yaml(f) if f.suffix == ".yaml" else read_jsonl(f)

cost_per_1k = cost.cost_columns
freshness_score = freshness.freshness

def write_alert(report):
    """Write over_cap_projected.json to alerts/ (not committed) and print it to the job log."""
    f = ROOT / "alerts" / runner.OVER_CAP_FILE; f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(f"ALERT {runner.OVER_CAP_FILE}: {report['reason']}", file=sys.stderr); print(json.dumps(report, sort_keys=True), file=sys.stderr)

def refuse(report):
    """R41 alert for a hard problem (missing price or cap, unknown provider, live provider with no cap, unreadable counter): write
    over_cap_projected.json and fail the job, exit code 2. No provider call was made. A cap breach is not a refusal (R44.3)."""
    write_alert(report); return 2

def skipped_cap_month(runs_dir, month):
    """Spec R43/R44: month-to-date count of skipped_cap runs per provider (distinct run_id in bench/runs/<month>-*/_skipped_cap.jsonl).
    Mock rows are not counted."""
    seen = {}
    for f in sorted(Path(runs_dir).glob(f"{month}-*/{runner.SKIPPED_CAP_FILE}")):
        for r in read_jsonl(f):
            if r.get("status") == runner.SKIPPED_CAP and not r.get("mock"): seen.setdefault(r["provider"], set()).add(r["run_id"])
    return {pid: len(ids) for pid, ids in sorted(seen.items())}

def job_summary(counts, month):
    """Spec R43: the job summary shows month-to-date skipped_cap runs per provider ($GITHUB_STEP_SUMMARY in Actions; printed always)."""
    lines = [f"### skipped_cap runs, month to date ({month})", ""] + ([f"- {pid}: {n}" for pid, n in counts.items()] or ["- none"])
    print("\n".join(lines))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh: fh.write("\n".join(lines) + "\n")

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--mode", choices=["smoke", "full"], default="smoke")
    ap.add_argument("--reprobe-offset", type=int, choices=freshness.REPROBE_OFFSETS_H)
    ap.add_argument("--only-providers", help="comma-separated provider ids to call (others are not called and not listed)")
    ap.add_argument("--rebuild-only", metavar="RUN_DATE", help="no provider calls: rescore existing run logs and the saved run summary for RUN_DATE")
    ap.add_argument("--only-sets", help="comma-separated set names to run (others are not loaded)"); a = ap.parse_args()
    cfg = load_yaml(BENCH / "config.yaml"); provs = json.loads((ROOT / "collectors/providers.json").read_text())["providers"]
    pricing = load_yaml(BENCH / "pricing.yaml")["providers"]; tos = load_yaml(BENCH / "tos_matrix.yaml")["providers"]
    today = dt.date.today().isoformat(); sets = {}; skips = []; flags = []
    for name, (errs, warns) in validate.validate_by_set(BENCH / "sets", cfg).items():
        if errs:   # never skip silently: every skipped set is logged with its reason and shown on the site
            errs = [e.replace(str(ROOT) + "/", "") for e in errs]   # repo-relative paths only (no box paths in published files)
            skips.append({"scope": "set", "set": name, "status": "skipped_invalid_set", "reason": "; ".join(errs[:3]) + (f" (+{len(errs) - 3} more)" if len(errs) > 3 else "")})
            print("SKIP set", name, "->", skips[-1]["reason"]); continue
        f = BENCH / "sets" / ("browser_tasks.yaml" if name == "browser_tasks" else f"{name}.jsonl")
        rows = load_test_set(f.name)
        entry = {"rows": rows[:10] if (a.mode == "smoke" and not name.startswith("news_")) else rows, "sha": sha256_file(f), "flags": []}
        if name.startswith("news_"):
            entry["flags"] = tables.week_labels(len(rows), cfg)   # owner ruling 1: 'below minimum (n=X)' from the real count
            for fl in entry["flags"]: flags.append({"set": name, "flag": fl}); print("FLAG", name, fl)
        sets[name] = entry
    news = sorted(k for k in sets if k.startswith("news_"))
    for old in news[:-1]: sets.pop(old)   # only the latest week is run
    if news:   # browser providers fetch the same frozen week's article URLs (derived, the news file is not touched)
        fname, fset = newsfetch.from_news_file(BENCH / "sets" / f"{news[-1]}.jsonl"); sets[fname] = fset | {"flags": sets[news[-1]]["flags"]}
    if a.only_sets: sets = {k: v for k, v in sets.items() if k in a.only_sets.split(",")}
    only = None
    if a.reprobe_offset:   # §3: re-query only the misses of the latest news set at +6/+24/+72 h
        sets = {k: v for k, v in sets.items() if k.startswith("news_")}
        due = {}
        for name, sv in sets.items():
            for f in (BENCH / "runs").glob(f"*/*/{name}.jsonl"):
                probes = {}
                for r in read_jsonl(f):
                    if r["status"] in ("pass", "fail"): probes.setdefault(r["item_id"], []).append({"offset_h": r.get("offset_h", 0), "results": r.get("results", [])})
                due[f.parent.name] = set(freshness.misses_due(sv["rows"], probes, a.reprobe_offset))
        only = lambda pid, item_id: item_id in due.get(pid, set())
    if a.rebuild_only:   # comma-separated run dates; a later date's status for a (provider, category) replaces an earlier one
        dates = sorted(a.rebuild_only.split(",")); today = dates[-1]; merged = {}
        for day in dates:
            sd = json.loads((BENCH / "runs" / day / f"_run-offset{a.reprobe_offset or 0}.json").read_text())
            for p in sd["providers"]:
                k = (p["provider"], p["category"])
                if p["status"] == "ran" or merged.get(k, {}).get("status") != "ran": merged[k] = p | {"run_date": day}
        s = {"run_date": today, "run_dates": dates, "mock": False, "offset_h": a.reprobe_offset or 0, "providers": list(merged.values())}
    else:
      now = dt.datetime.now(dt.timezone.utc); unknown = []
      if a.only_providers:
          unknown = [f"{n}: unknown provider (not in collectors/providers.json)" for n in a.only_providers.split(",") if n not in {p["id"] for p in provs}]
          provs = [p for p in provs if p["id"] in a.only_providers.split(",")]
      sched = cfg.get("run_schedule") or {}; limits = None
      if not a.reprobe_offset and a.mode == "smoke":   # R44.1: Apify is not in the daily smoke run (it projects $0 Apify)
          out_smoke = [p["id"] for p in provs if p["id"] in sched.get("smoke_exclude", [])]
          if out_smoke: print(f"R44.1: not in the smoke run: {', '.join(out_smoke)}")
          provs = [p for p in provs if p["id"] not in out_smoke]
      if not a.reprobe_offset and a.mode == "full": limits = sched.get("full_limits")   # R44.2: Apify's weekly full run is trimmed
      probs = unknown + runner.provider_problems(provs, cfg, tos)   # R41.2: before the keyless check, so an unknown provider always fails the job
      if probs: return refuse(runner.refusal_report(today, now, probs))
      if not keyed_providers(provs, os.environ, cfg, tos):   # a run without keys must never overwrite published data or run summaries
          print(f"SKIP publish: no provider API key present for any collector ({today}); no provider calls made; "
                "data/benchmarks/latest.json and bench/runs left unchanged"); return 0
      try:
       s = runner.run(providers=provs, sets=sets, pricing=run_pricing(load_yaml(BENCH / "pricing.yaml")), tos=tos, adapters=ADAPTERS, cfg=cfg, runs_dir=BENCH / "runs",
                   ledger_path=ROOT / "data" / "spend" / f"{today[:7]}-quota.json", run_date=today, env=os.environ, spend_dir=BENCH / "spend", guard_dir=ROOT / "data" / "spend",
                   offset_h=a.reprobe_offset or 0, only_ids=only, limits=limits,
                   meters={"apify": lambda: apify_meter(os.environ["APIFY_API_TOKEN"]),
                           "contextdev": lambda: contextdev_meter(os.environ["CONTEXT_DEV_API_KEY"])})
      except runner.RunRefused as e: return refuse(e.report)   # R41.1: no price/cap, unknown provider, live with no cap: 0 calls, job fails (exit 2)
      if s.get("over_cap_projected"): write_alert(s["over_cap_projected"])   # R44.3: providers dropped on cap; the others ran, exit 0
      job_summary(skipped_cap_month(BENCH / "runs", today[:7]), today[:7])
    for p in s["providers"]: print(json.dumps(p))
    doc = tables.build(BENCH / "runs", pricing, BENCH / "sets", tos, cfg)
    out = ROOT / "data" / "benchmarks" / "latest.json"
    finish_doc(doc, s, cfg, out, skips)
    if not sets: skips.append({"scope": "run", "status": "no_valid_sets", "reason": "no test set passed validation; no provider calls made"})
    doc["run_summary"] = s; doc["run_date"] = today; doc["run_dates"] = sorted({r["run_date"] for t in doc["tables"].values() for r in t if r.get("run_date")}) or [today]; doc["sets_valid"] = sorted(sets); doc["skips"] = skips; doc["flags"] = flags
    merge_previous(doc, s, out, set(sets) | {k["set"] for k in skips if k.get("scope") == "set"})
    held = tables.held_providers(cfg, pricing, tos) | {p["provider"] for p in s["providers"] if not gates.vendor_confirmed(tos.get(p["provider"]))}   # R10 shared gate
    tables.strip_held(doc, held)   # publish_hold / P0-P1 gate: run logs stay, latest.json never shows them
    if not a.rebuild_only and not fresh_data(s, held):   # at least one non-stale call must have returned data
        print(f"SKIP publish: no provider call returned publishable data ({today}); data/benchmarks/latest.json and the skips log left unchanged"); return 0
    (BENCH / "runs" / today).mkdir(parents=True, exist_ok=True)
    (BENCH / "runs" / today / f"_skips-offset{a.reprobe_offset or 0}.json").write_text(json.dumps({"skips": skips, "flags": flags}, indent=1))
    out.parent.mkdir(parents=True, exist_ok=True)
    probs = tables.untraced_cells(doc, BENCH)
    if probs: print("untraced cells:", probs); return 1
    out.write_text(json.dumps(doc, indent=1)); print("wrote", out, "has_results:", doc["has_results"]); return 0

if __name__ == "__main__": sys.exit(main())
