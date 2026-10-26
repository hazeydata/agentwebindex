"""AWI-PROVIDER-KEENABLE-001 phase K1 (ACs 1-8, 17-23; R1-R3). MOCK ONLY: urlopen is always patched; nothing calls Keenable."""
import ast, copy, datetime as dt, json, re, sys, unittest, urllib.error
from pathlib import Path
from unittest import mock
BENCH = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(BENCH)); sys.path.insert(0, str(Path(__file__).parent))
from awibench import runner, tables, guard, gates, confirmation_diff as cd
from awibench.util import load_yaml, read_jsonl, SITE
import mocks

FX = Path(__file__).parent / "fixtures" / "keenable"
CFG = load_yaml(BENCH / "config.yaml"); TOS_DOC = load_yaml(BENCH / "tos_matrix.yaml"); TOS = TOS_DOC["providers"]
PRICING_DOC = load_yaml(BENCH / "pricing.yaml")
REC = {"date": "2026-10-25", "sender": "@styskin", "quote": "mock quote", "evidence_path": "mock/evidence.json", "recorded_pr": "https://github.com/hazeydata/agentwebindex/pull/0"}
PROV = {"id": "keenable", "name": "Keenable", "categories": ["search"], "key_env": None}
SENTINELS = ("keensent-url-1.example", "keensent-url-2.example", "KEENSENT-SNIPPET", "KEENSENT-T")
sys.path.insert(0, str(SITE / "collectors")); import benchmark as B


class Base(unittest.TestCase):
    TS = "2026-10-26T10:00:00+00:00"
    def setUp(self):
        self.tmp = self.tmp_path; self.spend = self.tmp / "spend"; self.spend.mkdir(); self.calls = []; self.sleeps = []
        self.cfg = mocks.mock_cfg(CFG); self.cfg["free_tier_guard"]["keenable"].update(live_run_allowed=True, vendor_confirmation=dict(REC))
        self.tos = copy.deepcopy(TOS); self.tos["keenable"]["vendor_confirmation"] = dict(REC)
        self.tos["keenable"]["benchmarking"] = {"status": "allowed", "quote": "fixture: CEO ruling OQ-K5 (committed tos keeps forbidden; K3 decides)"}
        sd = self.tmp / "sets"; mocks.make_sets(sd); self.rows = read_jsonl(sd / "search_evergreen.jsonl")
        self.pricing = {"keenable": {"search": mocks.pricing_fixture("credits")}}
    def ok(self, key, item):
        self.calls.append(item["id"]); d = json.loads((FX / "search_public_ok.json").read_text())
        return {"http_status": 200, "billed_units": 0, "results": [{"url": r["url"], "title": r["title"], "snippet": r["snippet"], "published_at": r["published_at"]} for r in d["results"]]}
    def go(self, n=3, fn=None, cfg=None, tos=None, rows=None, ts=None):
        return runner.run(providers=[PROV], sets={"search_evergreen": {"rows": rows or self.rows[:n], "sha": "x"}}, pricing=self.pricing, tos=self.tos if tos is None else tos,
                          adapters={"keenable": {"search": fn or self.ok}}, cfg=cfg or self.cfg, runs_dir=self.tmp / "runs", ledger_path=self.tmp / "ledger.json",
                          run_date="2026-10-26", env={}, mock=True, spend_dir=self.spend, now_fn=lambda: dt.datetime.fromisoformat(ts or self.TS), sleep=self.sleeps.append)
    def prov(self, s): return next(p for p in s["providers"] if p["provider"] == "keenable")
    def cfg_with(self, **g): c = copy.deepcopy(self.cfg); c["free_tier_guard"]["keenable"].update(g); return c
    def lines(self): return read_jsonl(self.tmp / "runs/2026-10-26/keenable/search_evergreen.jsonl")


class K1Committed(Base):
    def test_ac1_tos_closed(self):
        k = TOS["keenable"]; self.assertIs(k[gates.REQUIRED_FLAG], True); self.assertIsNone(k["vendor_confirmation"]); self.assertFalse(gates.vendor_confirmed(k))
    def test_ac2_config_mirrors_contextdev(self):
        g, c = CFG["free_tier_guard"]["keenable"], CFG["free_tier_guard"]["contextdev"]
        self.assertIs(g["live_run_allowed"], False); self.assertIsNone(g["vendor_confirmation"]); self.assertEqual(g["max_concurrent"], c["max_concurrent"])
        self.assertEqual(g["max_concurrent"], 1); self.assertIs(g["paid_usage_allowed"], False); self.assertIs(g["paid_tier"], False); self.assertIs(g["publish_raw_results"], False)
        self.assertEqual((g["quota_credits"], g["stop_at_credits"], g["window"]), (900, 899, "utc_hour"))
        self.assertEqual(g["rate_limit"], {"max_per_second": 9, "max_per_hour": 899}); self.assertLess(g["rate_limit"]["max_per_hour"], 900)
        self.assertEqual(g["endpoint"], "https://api.keenable.ai/v1/search/public"); self.assertIn("keenable", CFG["month1_free_tier_providers"])
    def test_ac3_live_allowed_generalized(self):
        la = guard.live_allowed; ok = {"free_tier_guard": {"keenable": {"live_run_allowed": True, "vendor_confirmation": dict(REC), "paid_usage_allowed": False}}}
        self.assertFalse(la(CFG, TOS, "keenable")); self.assertFalse(la(ok, TOS | {}, "keenable") and False)
        self.assertTrue(la(ok, self.tos, "keenable")); self.assertTrue(la(ok, TOS, "keenable"))   # null tos record: only the config record counts (R22)
        for k, v in (("live_run_allowed", "true"), ("live_run_allowed", 1), ("live_run_allowed", False), ("vendor_confirmation", "yes"), ("vendor_confirmation", {}),
                     ("vendor_confirmation", None), ("paid_usage_allowed", True), ("paid_usage_allowed", "false"), ("paid_usage_allowed", 0), ("paid_usage_allowed", None)):
            c = copy.deepcopy(ok); c["free_tier_guard"]["keenable"][k] = v; self.assertFalse(la(c, self.tos, "keenable"), (k, v))
        c = copy.deepcopy(ok); del c["free_tier_guard"]["keenable"]["paid_usage_allowed"]; self.assertFalse(la(c, self.tos, "keenable"))   # R1: missing fails closed
        for t in ({}, None, {"keenable": None}, {"keenable": self.tos["keenable"] | {gates.REQUIRED_FLAG: False}}, {"keenable": {k: v for k, v in self.tos["keenable"].items() if k != gates.REQUIRED_FLAG}},
                  {"keenable": self.tos["keenable"] | {"vendor_confirmation": "yes"}}):
            self.assertFalse(la(ok, t, "keenable"), t)
        self.assertFalse(la(CFG, TOS)); self.assertFalse(la(CFG, TOS, "contextdev"))   # contextdev default id unchanged
    def test_ac5_committed_config_zero_calls(self):
        fn = mock.Mock(side_effect=self.ok)
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")) as net:
            s = self.go(fn=fn, cfg=mocks.mock_cfg(CFG), tos=TOS)
        self.assertEqual(self.prov(s)["status"], "skipped_no_vendor_confirmation"); fn.assert_not_called(); net.assert_not_called()
        self.assertFalse((self.tmp / "runs").exists()); self.assertFalse(list(self.spend.glob("keenable-*")))
        s = self.go(fn=fn, cfg=mocks.mock_cfg(CFG), tos=self.tos)   # tos record set, committed config gate still closed
        self.assertEqual(self.prov(s)["status"], "skipped_no_vendor_confirmation"); fn.assert_not_called()
        self.assertEqual(B.keyed_providers([p for p in json.loads((SITE / "collectors/providers.json").read_text())["providers"] if p["id"] == "keenable"], {}), [])   # never picked for a live run in K1
    def test_ac7_publish_gate_real_files(self):
        held = tables.held_providers(CFG, PRICING_DOC["providers"], TOS); self.assertIn("keenable", held)
        self.assertNotIn("keenable", PRICING_DOC["providers"])   # AC21: price record kept outside providers -> never a Prices row or cost column
        latest = json.loads((SITE / "data/benchmarks/latest.json").read_text()); self.assertNotIn("keenable", json.dumps(latest).lower())
        for f in (SITE / "data").rglob("*"):
            if f.is_file() and f.suffix in (".json", ".csv"): self.assertNotIn("keenable", f.read_text(errors="replace").lower(), f)
        for f in (SITE / "dist").rglob("*") if (SITE / "dist").is_dir() else []:
            if f.is_file() and f.suffix in (".json", ".csv", ".html", ".js"): self.assertNotIn("keenable", f.read_text(errors="replace").lower(), f)
        provs = json.loads((SITE / "collectors/providers.json").read_text())["providers"]
        names = tables.also_priced_providers(PRICING_DOC["providers"], latest, provs); self.assertNotIn("Keenable", names)
        unflagged = [{k: v for k, v in p.items() if k != "exclude_from_also_priced"} for p in provs]
        self.assertNotIn("Keenable", tables.also_priced_providers(PRICING_DOC["providers"], latest, unflagged))   # not in pricing providers either: two independent locks
        self.assertIs(next(p for p in provs if p["id"] == "keenable")["exclude_from_also_priced"], True)
    def test_ac22_no_price_snapshot_even_when_confirmed(self):
        import snapshot_prices as SP
        provs = json.loads((SITE / "collectors/providers.json").read_text())["providers"]
        tos = copy.deepcopy(TOS); tos["keenable"]["vendor_confirmation"] = dict(REC)   # even after a K2 flip
        self.assertNotIn("keenable", [p["id"] for p in SP.snapshot_providers(provs, tos)])
    def test_ac21_price_record(self):
        r = PRICING_DOC["keenable_price_record"]
        self.assertEqual((r["usd_per_1k_requests"], r["source_url"], r["checked_at"]), (4.0, "https://keenable.ai/pricing", "2026-10-26"))
        self.assertIn("$4 / 1000 requests", r["quote"])


class K1Adapter(Base):
    SRC = (SITE / "collectors/benchmark.py").read_text()
    def _kfuncs(self):
        tree = ast.parse(self.SRC); return [n for n in tree.body if isinstance(n, ast.FunctionDef) and "keenable" in n.name]
    def test_ac6_ac18_ac22_only_public_post(self):
        self.assertEqual(B.KEENABLE_SEARCH_URL, "https://api.keenable.ai/v1/search/public"); self.assertEqual(B.ADAPTERS["keenable"], {"search": B._keenable_search})
        src = "\n".join(ast.get_source_segment(self.SRC, f) for f in self._kfuncs())
        self.assertEqual(re.findall(r'method="(\w+)"', src), ["POST"]); self.assertNotRegex(src, r"\b(PUT|PATCH|DELETE)\b")
        self.assertEqual(re.findall(r"https?://[^\s\"']+", src), [])   # no URL literal inside the adapter: only the module constant
        urls = set(re.findall(r"https?://[^\s\"')]*keenable[^\s\"')]*", "\n".join(l for l in self.SRC.splitlines() if not l.lstrip().startswith("#"))))
        self.assertEqual(urls, {"https://api.keenable.ai/v1/search/public"})   # no keyed /v1/search, no x402, no keenable.ai page
        self.assertNotRegex(src, r"X-API-Key|Authorization|environ|getenv")   # keyless: no key read at all (AC18 supersedes AC6's env var)
        seen = []
        class R:
            status = 200
            def read(s): return (FX / "search_public_ok.json").read_bytes()
            def __enter__(s): return s
            def __exit__(s, *a): return False
        with mock.patch("urllib.request.urlopen", side_effect=lambda req, timeout=None: seen.append(req) or R()):
            out = B._keenable_search("keyless", {"id": "i", "query": "q1"})
        (req,) = seen; self.assertEqual((req.full_url, req.get_method()), ("https://api.keenable.ai/v1/search/public", "POST"))
        self.assertEqual(req.get_header("X-keenable-title"), "AgentWebIndex"); self.assertIsNone(req.get_header("X-api-key")); self.assertIsNone(req.get_header("Authorization"))
        self.assertEqual(json.loads(req.data), {"query": "q1"}); self.assertEqual(len(out["results"]), 2); self.assertNotIn("usage", out)
    def test_ac20_adapter_surfaces_usage_block(self):
        class R:
            status = 200
            def read(s): return (FX / "search_billed.json").read_bytes()
            def __enter__(s): return s
            def __exit__(s, *a): return False
        with mock.patch("urllib.request.urlopen", return_value=R()): out = B._keenable_search("keyless", {"id": "i", "query": "q"})
        self.assertTrue(out["usage"]["paid"]); self.assertTrue(guard.paid_stop("keenable", 200, out))
    def test_adapter_http_error_has_no_vendor_text(self):
        e = urllib.error.HTTPError(B.KEENABLE_SEARCH_URL, 429, "x", {"Retry-After": "2"}, None); e.read = lambda *a: b"KEENSENT-SNIPPET leaked"
        with mock.patch("urllib.request.urlopen", side_effect=e), self.assertRaises(RuntimeError) as cm: B._keenable_search("keyless", {"id": "i", "query": "q"})
        self.assertEqual(str(cm.exception), "HTTP 429: Retry-After: 2; keenable error")


class K1Guard(Base):
    def test_ac4_serial_semaphore_and_peak_one(self):
        made = []; real = guard.make_semaphore
        with mock.patch.object(runner.guard, "make_semaphore", side_effect=lambda n: made.append(n) or real(n)): s = self.go(n=4)
        self.assertEqual((made, self.prov(s)["status"], len(self.calls)), ([1], "ran", 4))
        import concurrent.futures, threading, time
        sem = guard.make_semaphore(CFG["free_tier_guard"]["keenable"]["max_concurrent"]); lock = threading.Lock(); cur = [0]; peak = [0]
        def fn(k, i):
            with lock: cur[0] += 1; peak[0] = max(peak[0], cur[0])
            time.sleep(0.01)
            with lock: cur[0] -= 1
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex: list(ex.map(lambda i: guard.guarded_call(sem, fn, "k", i), range(10)))
        self.assertEqual(peak[0], 1)
    def test_ac4_counter_per_utc_hour_and_unreadable_fails_closed(self):
        self.go(n=3); f = self.spend / "keenable-2026-10-26T10.json"; self.assertEqual(json.loads(f.read_text())["attempted"], 3)
        f.write_text("{not json"); n0 = len(self.calls); s = self.go(n=2)
        self.assertEqual((self.prov(s)["status"], len(self.calls) - n0), ("stale_budget", 0))
        f.write_text(json.dumps({"provider": "keenable", "window": "2026-10-26T10", "attempted": 898, "billed": 0})); n0 = len(self.calls); s = self.go(n=3)
        self.assertEqual((self.prov(s)["status"], len(self.calls) - n0), ("stale_quota", 1))   # 899th allowed, 900th never
        s = self.go(n=2, ts="2026-10-26T11:00:00+00:00"); self.assertEqual(self.prov(s)["status"], "ran")   # next UTC hour: fresh counter
    def test_r1_paid_usage_flag_missing_or_nonbool_zero_calls(self):
        for v in (True, "false", 0, None, ...):
            c = self.cfg_with() if v is ... else self.cfg_with(paid_usage_allowed=v)
            if v is ...: del c["free_tier_guard"]["keenable"]["paid_usage_allowed"]
            fn = mock.Mock(side_effect=self.ok); s = self.go(fn=fn, cfg=c)
            self.assertEqual(self.prov(s)["status"], "stopped_paid_usage", v); fn.assert_not_called()
    def _stop_case(self, first):
        n = [0]
        def fn(key, item):
            n[0] += 1
            return first(key, item) if n[0] == 2 else self.ok(key, item)
        s = self.go(n=6, fn=fn); return s, n[0]
    def test_ac20_billed_response_stops_all_calls(self):
        s, n = self._stop_case(lambda k, i: {"http_status": 200, "results": [], "usage": {"sku": "search.pro", "credits": 1, "paid": True}})
        self.assertEqual((self.prov(s)["status"], n), ("stopped_paid_usage", 2)); self.assertEqual(self.lines()[-1]["status"], "paid_usage_stop")
        self.assertTrue(all(l["run_status"] == "stopped_paid_usage" for l in self.lines()))
        s, n = self._stop_case(lambda k, i: {"http_status": 200, "results": [], "billed_units": 1}); self.assertEqual((self.prov(s)["status"], n), ("stopped_paid_usage", 2))
    def test_ac20_exhausted_quota_402_stops_all_calls(self):
        def e402(k, i): raise RuntimeError("HTTP 402: keenable error")
        s, n = self._stop_case(e402); self.assertEqual((self.prov(s)["status"], n), ("stopped_paid_usage", 2))
        self.assertIn("402", self.prov(s)["reason"])
    def test_ac19_429_one_retry_then_rate_limited(self):
        calls = []
        def r429(k, i): calls.append(i["id"]); raise RuntimeError("HTTP 429: Retry-After: 3; keenable error")
        s = self.go(n=4, fn=r429); self.assertEqual((self.prov(s)["status"], len(calls)), ("rate_limited", 2)); self.assertEqual(self.sleeps, [3])
        calls.clear(); self.sleeps.clear()
        def bare429(k, i): calls.append(1); raise RuntimeError("HTTP 429: keenable error")
        s = self.go(n=4, fn=bare429, ts="2026-10-26T12:00:00+00:00"); self.assertEqual((self.prov(s)["status"], len(calls), self.sleeps), ("rate_limited", 1, []))
        seq = iter([RuntimeError("HTTP 429: Retry-After: 1; keenable error")])
        def once(k, i):
            e = next(seq, None)
            if e: raise e
            return self.ok(k, i)
        s = self.go(n=5, fn=once, ts="2026-10-26T13:00:00+00:00"); self.assertEqual(self.prov(s)["status"], "ran")   # retry succeeded: run completes (1 of 6 lines is a 429, under the 20% quota-degraded rule)
    def test_ac19_limiter_per_second_and_hour(self):
        t = [0.0]; slept = []
        lim = guard.RateLimiter(9, 899, clock=lambda: t[0], sleep=slept.append)
        for _ in range(9): self.assertTrue(lim.acquire())
        self.assertEqual(slept, []); self.assertTrue(lim.acquire()); self.assertAlmostEqual(slept[0], 1.0)   # 10th waits a full second
        stamps = sorted(lim.t); self.assertTrue(all(sum(1 for y in stamps if x <= y < x + 1.0) <= 9 for x in stamps))
        lim = guard.RateLimiter(9, 899, clock=lambda: t[0], sleep=lambda s: None)
        got = sum(lim.acquire() for _ in range(1000)); self.assertEqual(got, 899)   # stops before 900 (and well before 1,000) per rolling hour
        stamps = sorted(lim.t); self.assertTrue(all(sum(1 for y in stamps if x <= y < x + 1.0) <= 9 for x in stamps))
        t[0] = stamps[0] + lim.skew + 3600.01; self.assertTrue(lim.acquire())   # the oldest call left the rolling hour
        for bad in (0, -1, 1.5, None): self.assertRaises(ValueError, guard.RateLimiter, bad, 899)
    def test_ac19_runner_stops_before_hourly_cap(self):
        c = self.cfg_with(rate_limit={"max_per_second": 9, "max_per_hour": 5}); s = self.go(n=8, cfg=c)
        self.assertEqual((self.prov(s)["status"], len(self.calls)), ("rate_limited", 5)); self.assertIn("rolling hour", self.prov(s)["reason"])
        self.assertTrue(self.sleeps == [] or all(x > 0 for x in self.sleeps))


class K1PublicOutputs(Base):
    def test_ac23_no_raw_urls_or_snippets_in_public_outputs(self):
        s = self.go(n=4); self.assertEqual(self.prov(s)["status"], "ran")
        blob = (self.tmp / "runs").rglob("*")
        logs = "\n".join(f.read_text() for f in blob if f.is_file())
        for x in SENTINELS: self.assertNotIn(x, logs)   # run logs (bench/runs is a public repo path) carry counts + hashes only
        self.assertTrue(all(l["results_count"] == 2 and "results" not in l for l in self.lines()))
        cfg = {"month1_free_tier_providers": ["keenable"], "publish_hold": [], "publish_gate": []}
        doc = tables.build(self.tmp / "runs", self.pricing, self.tmp / "sets", self.tos, cfg); doc["run_summary"] = s
        tables.strip_held(doc, tables.held_providers(cfg, self.pricing, self.tos))
        csv = "\n".join(",".join(str(v) for v in r.values()) for tb in doc.get("tables", {}).values() for r in tb)
        for x in SENTINELS: self.assertNotIn(x, json.dumps(doc, default=str)); self.assertNotIn(x, csv)
        for x in SENTINELS: self.assertNotIn(x, json.dumps(s, default=str))
    def test_contextdev_still_keeps_results(self):   # the redaction is keenable-only
        c = mocks.mock_cfg(CFG); self.assertNotIn("publish_raw_results", c["free_tier_guard"]["contextdev"])


class K1ConfirmationDiff(unittest.TestCase):
    def test_ac8_fixture_cases_all_fail(self):
        base = {cd.TOS: copy.deepcopy(TOS_DOC), cd.CFG: copy.deepcopy(CFG)}
        self.assertEqual(cd.check([cd.TOS, cd.CFG], base, copy.deepcopy(base)), [])   # control
        for c in json.loads((FX / "confirmation_cases.json").read_text())["cases"]:
            head = copy.deepcopy(base); node = head[c["file"]]
            for k in c["path"][:-1]: node = node[k]
            if c["op"] == "delete": del node[c["path"][-1]]
            else: node[c["path"][-1]] = c["value"]
            self.assertNotEqual(cd.check([cd.TOS, cd.CFG], base, head), [], c["name"])
    def test_ac8_schema_covers_keenable_entries(self):
        head = {cd.TOS: copy.deepcopy(TOS_DOC), cd.CFG: copy.deepcopy(CFG)}; self.assertEqual(cd.schema_errors(head), [])
        head[cd.CFG]["free_tier_guard"]["keenable"]["vendor_confirmation"] = {"date": "2026-10-25"}
        self.assertTrue(any("keenable" in e for e in cd.schema_errors(head)))


if __name__ == "__main__": unittest.main()
