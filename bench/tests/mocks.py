"""MOCK data for tests only. Nothing here may be written to site/data, site/dist or bench/runs."""
import hashlib, json, random
from pathlib import Path

DEMO = "demo-sandbox.example"
EVERGREEN_Q = {"facts/reference": 30, "docs/API lookup": 25, "local/entity": 10, "academic": 10, "long-tail": 15, "non-English": 10}
SCRAPE_Q = {"static": 50, "js_heavy": 40, "paywall_lite": 30, "anti_bot": 30}
TASKS_Q = {"forms": 5, "multi_page_navigation": 5, "search_and_extract": 5, "js_widgets": 3, "file_download": 2}
CHECKS = ["url_domain", "url_exact", "snippet_contains"]

def make_sets(d):
    d = Path(d); d.mkdir(parents=True, exist_ok=True); n = 0; ev = []
    for cat, q in EVERGREEN_Q.items():
        for _ in range(q):
            n += 1; t = CHECKS[n % 3]
            val = {"url_domain": f"answer{n}.example", "url_exact": f"https://answer{n}.example/page", "snippet_contains": f"mock answer {n}"}[t]
            ev.append({"id": f"ev-{n:03d}", "query": f"mock query {n}", "category": cat, "check": {"type": t, "value": val},
                       "source_url": f"https://answer{n}.example/page", "added": "2026-10-15"})
    sc = []; n = 0
    for cls, q in SCRAPE_Q.items():
        for _ in range(q):
            n += 1
            sc.append({"id": f"sc-{n:03d}", "url": f"https://scrape{n}.example/a", "class": cls,
                       "marker": {"type": "text_contains", "value": f"marker phrase {n}"} if n % 5 else {"type": "selector_text", "value": f"div.content::marker phrase {n}"},
                       "min_chars": 5000 if cls == "paywall_lite" else 50, "robots_ok": True, "added": "2026-10-15"})
    ts = []; n = 0; types = ["final_url_matches", "extracted_value_equals", "file_sha256", "dom_text_contains"]
    for cat, q in TASKS_Q.items():
        for _ in range(q):
            n += 1; t = types[n % 4]
            v = {"final_url_matches": f"https://{DEMO}/done/{n}", "extracted_value_equals": f"value-{n}",
                 "file_sha256": hashlib.sha256(f"file-{n}".encode()).hexdigest(), "dom_text_contains": f"success {n}"}[t]
            ts.append({"id": f"bt-{n:02d}", "start_url": f"https://{DEMO}/task/{n}", "goal": f"mock goal {n}", "steps_hint": ["open", "act"],
                       "pass": {"type": t, "value": v}, "timeout_s": 120, "category": cat})
    (d / "search_evergreen.jsonl").write_text("".join(json.dumps(r) + "\n" for r in ev))
    (d / "scrape_urls.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sc))
    (d / "browser_tasks.yaml").write_text("# MOCK\n" + json.dumps(ts, indent=1))
    return ev, sc, ts

def mock_cfg(base_cfg):
    c = json.loads(json.dumps(base_cfg)); c["task_site_allowlist"] = [DEMO]; c.pop("freeze_manifest", None)
    c["month1_free_tier_providers"] = None   # mock provider ids aren't real; month-one list is tested separately
    return c

def pricing_fixture(model, **tiers_override):
    base = {"per_request": {"entry": {"name": "PAYG", "unit_price": 0.005}, "volume": {"name": "Scale", "unit_price": 0.003}},
            "credits": {"entry": {"name": "Hobby", "usd_per_credit": 0.008}, "volume": {"name": "Growth", "usd_per_credit": 0.004}},
            "tier": {"entry": {"name": "Starter", "plan_fee": 20.0, "included_units": 5000, "overage_per_unit": 0.005},
                     "volume": {"name": "100k", "plan_fee": 100.0, "included_units": 100000, "overage_per_unit": 0.001}},
            "per_hour_browser": {"entry": {"name": "Dev", "usd_per_hour": 0.12, "min_minutes": 1, "rounding_minutes": 1},
                                 "volume": {"name": "Startup", "usd_per_hour": 0.10, "min_minutes": 1, "rounding_minutes": 1}},
            "per_gb": {"entry": {"name": "PAYG", "usd_per_gb": 10.0}, "volume": {"name": "Bulk", "usd_per_gb": 8.0}}}[model]
    base.update(tiers_override)
    return {"model": model, "credits_per_call": {"search": 2, "scrape": 1}, "proxy_per_gb": 10.0, "checked_at": "MOCK",
            "free_tier": {"units_per_month": 1000, "needs_card": False, "pool": None, "note": "MOCK free tier: 1000 units/month"}, "tiers": base}

class MockAdapters:
    """Deterministic fake provider behaviour. pass_rate in [0,1]; seeded per item id."""
    def __init__(self, pass_rate=0.7, seed=1, units=None, latency=None):
        self.pass_rate, self.seed, self.units, self.calls = pass_rate, seed, units, 0
    def _ok(self, item): return random.Random(f"{self.seed}|{item['id']}").random() < self.pass_rate
    def search(self, key, item):
        self.calls += 1; ok = self._ok(item); ch = item["check"]; res = []
        if ok:
            if ch["type"] == "snippet_contains": res = [{"url": "https://x.example/1", "title": ch["value"], "snippet": ""}]
            elif ch["type"] == "url_domain": res = [{"url": f"https://{ch['value']}/r", "title": "", "snippet": ""}]
            else: res = [{"url": ch["value"], "title": "", "snippet": "", "published_at": item.get("published_at")}]
        res += [{"url": f"https://filler{i}.example/", "title": "filler", "snippet": ""} for i in range(10 - len(res))]
        out = {"results": res, "http_status": 200}
        if self.units is not None: out["billed_units"] = self.units
        return out
    def scrape(self, key, item):
        self.calls += 1; ok = self._ok(item); phrase = item["marker"]["value"].split("::")[-1]
        body = ("lorem ipsum " * max(10, item["min_chars"] // 12 + 1)) + (phrase if ok else "")
        return {"text": body, "html": f'<div class="content">{body}</div>', "http_status": 200}
    def browser(self, key, item):
        self.calls += 1; ok = self._ok(item); t, v = item["pass"]["type"], item["pass"]["value"]
        out = {"final_url": "https://nowhere.example/", "extracted_value": None, "dom_text": "", "billed_units": 1.5}
        if ok:
            if t == "final_url_matches": out["final_url"] = v
            elif t == "extracted_value_equals": out["extracted_value"] = v
            elif t == "dom_text_contains": out["dom_text"] = f"... {v} ..."
            else: out["file_bytes"] = f"file-{item['id'].split('-')[1].lstrip('0')}".encode()
        return out
    def table(self, pid, cats): return {pid: {c: getattr(self, c) for c in cats}}

# R25 (Keenable R4 (1)): gate-behavior tests use explicit closed/open fixtures, never the committed state, so a K2 flip of
# bench/config.yaml + bench/tos_matrix.yaml cannot turn a closed-state assertion off. Committed-state tests use keenable_committed_check.
def keenable_closed(cfg=None, tos_providers=None):
    """Deep copies of (cfg, tos providers) with keenable forced to the K1 closed defaults (live_run_allowed false, both records null)."""
    c = json.loads(json.dumps(cfg)) if cfg is not None else None; t = json.loads(json.dumps(tos_providers)) if tos_providers is not None else None
    if c is not None: c["free_tier_guard"]["keenable"].update(live_run_allowed=False, vendor_confirmation=None)
    if t is not None: t["keenable"]["vendor_confirmation"] = None
    return c, t

def keenable_committed_open(cfg):
    return cfg["free_tier_guard"]["keenable"]["live_run_allowed"] is True

def keenable_committed_check(tc, cfg, tos_providers):
    """Consistency check on the committed files. Open (live_run_allowed true): the config record passes gates.record_error, the tos
    entry stays required and its record passes record_error and equals the config record, paid usage stays false. Closed: the K1
    closed assertions (bool false, both records null, not vendor-confirmed). Returns True when open."""
    from awibench import gates
    g, k = cfg["free_tier_guard"]["keenable"], tos_providers["keenable"]
    tc.assertIs(k[gates.REQUIRED_FLAG], True); tc.assertIs(g["paid_usage_allowed"], False)
    if keenable_committed_open(cfg):
        tc.assertIsNone(gates.record_error(g["vendor_confirmation"])); tc.assertIsNone(gates.record_error(k["vendor_confirmation"]))
        tc.assertEqual(g["vendor_confirmation"], k["vendor_confirmation"]); tc.assertTrue(gates.vendor_confirmed(k))
        return True
    tc.assertIs(g["live_run_allowed"], False); tc.assertIsNone(g["vendor_confirmation"]); tc.assertIsNone(k["vendor_confirmation"])
    tc.assertFalse(gates.vendor_confirmed(k))
    return False
