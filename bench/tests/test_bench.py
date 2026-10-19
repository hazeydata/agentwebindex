"""SPEC-bench AC tests. MOCK data only; all outputs go to a temp dir. Run: python -m unittest -v (cwd bench/)."""
import datetime as dt, hashlib, json, math, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
BENCH = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(BENCH)); sys.path.insert(0, str(Path(__file__).parent))
from awibench import validate, newsgen, cost, freshness, runner, tables, secrets_scan, freeze, checks
from awibench.util import load_yaml, read_jsonl, sha256_file, SITE
import mocks

CFG = load_yaml(BENCH / "config.yaml"); RSS = Path(__file__).parent / "fixtures" / "rss"
RUN_AT = dt.datetime.fromisoformat("2026-10-19T06:00:00-04:00")

class T(unittest.TestCase):
    def setUp(self): self.tmp = Path(tempfile.mkdtemp(prefix="awi-test-")); self.cfg = mocks.mock_cfg(CFG)

    # ---------- AC1 / AC4 validator ----------
    def test_ac1_valid_mock_sets_pass(self):
        mocks.make_sets(self.tmp); errs, _ = validate.validate(self.tmp, self.cfg); self.assertEqual(errs, [])
    def test_ac1_count_quota_schema_errors_exit_nonzero(self):
        for mutate in ("drop", "quota", "schema", "check_type"):
            d = self.tmp / mutate; ev, sc, ts = mocks.make_sets(d)
            if mutate == "drop": ev = ev[:-1]
            if mutate == "quota": ev[0]["category"] = "academic"
            if mutate == "schema": del ev[3]["source_url"]
            if mutate == "check_type": ev[4]["check"]["type"] = "fuzzy"
            (d / "search_evergreen.jsonl").write_text("".join(json.dumps(r) + "\n" for r in ev))
            cfgp = self.tmp / f"cfg-{mutate}.yaml"; cfgp.write_text(json.dumps(self.cfg))
            self.assertEqual(validate.main([str(d), "--config", str(cfgp)]), 1, mutate)
    def test_ac1_real_sets_dir_currently_fails(self):
        errs, _ = validate.validate(BENCH / "sets", CFG); self.assertTrue(any("missing" in e for e in errs))
    def test_ac4_robots_and_allowlist(self):
        ev, sc, ts = mocks.make_sets(self.tmp); sc[0]["robots_ok"] = False; ts[0]["start_url"] = "https://shop.example/buy"
        (self.tmp / "scrape_urls.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sc))
        (self.tmp / "browser_tasks.yaml").write_text(json.dumps(ts))
        errs, _ = validate.validate(self.tmp, self.cfg)
        self.assertTrue(any("robots_ok" in e for e in errs)); self.assertTrue(any("allowlist" in e for e in errs))

    # ---------- AC2 freeze ----------
    def test_ac2_freeze_logs_status_and_marker_health(self):
        ev, sc, _ = mocks.make_sets(self.tmp)
        good = freeze.freeze(self.tmp, self.cfg, lambda u: 200, lambda u: {"text": "x" * 60 + " marker phrase " + u.split("scrape")[1].split(".")[0], "html": ""}, "2026-10-15")
        # selector markers need html; supply html too
        def ref(u):
            n = u.split("scrape")[1].split(".")[0]; body = "x" * 60 + f" marker phrase {n}"
            return {"text": body, "html": f'<div class="content">{body}</div>'}
        good = freeze.freeze(self.tmp, self.cfg, lambda u: 200, ref, "2026-10-15")
        self.assertEqual(good["failures"], []); self.assertEqual(len(good["source_url"]), 100); self.assertEqual(len(good["markers"]), 150)
        bad = freeze.freeze(self.tmp, self.cfg, lambda u: 404 if "answer7." in u else 200, ref, "2026-10-15")
        self.assertTrue(any("ev-007" in f for f in bad["failures"]))
        freeze.retire(self.tmp, "scrape_urls.jsonl", "sc-001", "marker dead (page changed)", "2026-10-20")
        self.assertEqual(read_jsonl(self.tmp / "retired.jsonl")[0]["reason"], "marker dead (page changed)")

    # ---------- AC3 / AC13 news generator ----------
    def _gen(self, seed="7", cfg=None):
        log = []; rows, name = newsgen.generate(cfg or CFG, RUN_AT, seed, lambda f: (RSS / f"{f['id']}.xml").read_text(), log); return rows, name, log
    def test_ac3_deterministic_byte_identical(self):
        a, b = self.tmp / "a.jsonl", self.tmp / "b.jsonl"
        r1, name, _ = self._gen(); newsgen.write(r1, a); r2, _, _ = self._gen(); newsgen.write(r2, b)
        self.assertEqual(a.read_bytes(), b.read_bytes()); self.assertEqual(name, "news_2026-43.jsonl")
        r3, _, _ = self._gen(seed="8"); self.assertNotEqual([r["id"] + r["source_url"] for r in r1], [r["id"] + r["source_url"] for r in r3])
        with self.assertRaises(FileExistsError): newsgen.write(r1, a)   # frozen, never edited
    def test_validator_news_warning_edges(self):
        # M15/M16: pin validate.check_news warning boundaries (short week <50, below minimum <25)
        def warns(n):
            rows = [{"feed": f"f{i}", "check": {"type": "url_exact"}} for i in range(n)]; e, w = [], []
            validate.check_news(rows, CFG, e, w); self.assertEqual(e, []); return " ".join(w)
        self.assertEqual(CFG["news"]["total"], 50); self.assertEqual(CFG["news"]["min_total"], 25)
        self.assertEqual(warns(50), ""); self.assertIn("short week (n=49)", warns(49))
        self.assertIn("short week (n=25)", warns(25)); self.assertNotIn("below minimum", warns(25))
        self.assertIn("below minimum 25", warns(24))
    def test_ac3_caps_and_window(self):
        rows, _, log = self._gen(); errs, warns = [], []; validate.check_news(rows, CFG, errs, warns); self.assertEqual(errs, [])
        self.assertLessEqual(len(rows), 50); self.assertGreaterEqual(len(rows), 25)
        from collections import Counter
        self.assertLessEqual(max(Counter(r["feed"] for r in rows).values()), 15)
        self.assertNotIn("per_outlet", CFG["news"]); self.assertNotIn("per_section", CFG["news"])
        self.assertEqual(log[-1]["rows"], len(rows)); self.assertFalse(log[-1]["below_min"])
        for r in rows:
            age = (RUN_AT - dt.datetime.fromisoformat(r["published_at"])).total_seconds() / 3600
            self.assertTrue(24 <= age <= 72, age); self.assertNotIn(" - Mock Outlet", r["query"])
    def _news_dir(self, rows, tag, name="news_2026-43.jsonl", log=()):
        sd = self.tmp / tag; mocks.make_sets(sd); newsgen.write(rows, sd / name, newsgen.meta(rows, name, CFG, RUN_AT, "7", list(log))); return sd
    def test_news_meta_count_full_week(self):
        rows, name, log = self._gen(); sd = self._news_dir(rows, "full", name, log)
        m = json.loads((sd / name).with_suffix(".meta.json").read_text())
        self.assertEqual(m["count"], len(rows)); self.assertEqual(m["short_week"], len(rows) < 50); self.assertEqual(m["flags"], [] if len(rows) == 50 else [f"short week (n={len(rows)})"]); self.assertEqual(m["sha256"], sha256_file(sd / name))
        self.assertEqual(validate.validate(sd, self.cfg)[0], [])
    def test_short_week_is_valid_flagged_and_runs(self):
        rows, name, _ = self._gen(); few = rows[:24]; sd = self._news_dir(few, "short", name)
        m = json.loads((sd / name).with_suffix(".meta.json").read_text())
        self.assertTrue(m["short_week"]); self.assertEqual(m["flags"], ["short week (n=24)", "below minimum (n=24)"])
        errs, warns = validate.validate(sd, self.cfg)
        self.assertEqual(errs, []); self.assertTrue(any("short week (n=24)" in w for w in warns))
        cfgp = self.tmp / "c.yaml"; cfgp.write_text(json.dumps(self.cfg)); self.assertEqual(validate.main([str(sd), "--config", str(cfgp), "--only", "news"]), 0)
        # harness runs on it, and the flag reaches run log + tables
        kw, _, _ = self._setup_run(units_per_month=100000, providers=[{"id": "mocksearch", "categories": ["search"], "key_env": "K"}])
        kw["sets"] = {name[:-6]: {"rows": few, "sha": sha256_file(sd / name), "flags": m["flags"]}}
        self.assertEqual(runner.run(**kw)["providers"][0]["status"], "ran")
        lines = read_jsonl(kw["runs_dir"] / "2026-10-19" / "mocksearch" / name)
        self.assertEqual(len(lines), 24); self.assertTrue(all(l["set_flags"] == ["short week (n=24)", "below minimum (n=24)"] and l["set_rows"] == 24 for l in lines))
        doc = tables.build(kw["runs_dir"], kw["pricing"], sd)
        F24 = ["short week (n=24)", "below minimum (n=24)"]
        self.assertEqual(doc["tables"]["search"][0]["flags"], F24); self.assertEqual(doc["freshness"][0]["flags"], F24)
    def test_real_news_errors_still_fail(self):
        rows, name, _ = self._gen(); cfgp = self.tmp / "c.yaml"; cfgp.write_text(json.dumps(self.cfg))
        sd = self._news_dir(rows, "nometa", name); (sd / name).with_suffix(".meta.json").unlink()
        self.assertTrue(any("missing" in e and "meta" in e for e in validate.validate(sd, self.cfg)[0]))
        self.assertEqual(validate.main([str(sd), "--config", str(cfgp), "--only", "news"]), 1)
        sd = self._news_dir(rows, "mismatch", name); mf = (sd / name).with_suffix(".meta.json")
        m = json.loads(mf.read_text()); m["count"] += 1; mf.write_text(json.dumps(m))
        self.assertTrue(any("count" in e and "!=" in e for e in validate.validate(sd, self.cfg)[0]))
        self.assertEqual(validate.main([str(sd), "--config", str(cfgp), "--only", "news"]), 1)
        sd = self._news_dir(rows[:24], "flaglie", name); mf = (sd / name).with_suffix(".meta.json")
        m = json.loads(mf.read_text()); m["short_week"] = False; mf.write_text(json.dumps(m))
        self.assertTrue(any("short_week flag inconsistent" in e for e in validate.validate(sd, self.cfg)[0]))
        over = rows + [dict(rows[0], id=f"x{i}", feed="hn") for i in range(4)]       # hn already at 15 -> 19
        sd = self._news_dir(over, "caps", name); self.assertTrue(any("feed 'hn'" in e and "cap 15" in e for e in validate.validate(sd, self.cfg)[0]))
    def test_validate_by_set_isolates_bad_sets(self):
        rows, name, _ = self._gen(); sd = self._news_dir(rows, "iso", name); (sd / "scrape_urls.jsonl").unlink()
        by = validate.validate_by_set(sd, self.cfg)
        self.assertTrue(by["scrape_urls"][0]); self.assertEqual(by["search_evergreen"][0], []); self.assertEqual(by[name[:-6]][0], [])
    def test_evergreen_quota_spread(self):
        self.assertEqual(validate.EVERGREEN_Q, {"facts/reference": 30, "docs/API lookup": 25, "local/entity": 10, "academic": 10, "long-tail": 15, "non-English": 10})
    def test_evergreen_has_no_product_price(self):
        self.assertNotIn("product/price", validate.EVERGREEN_Q); self.assertEqual(sum(validate.EVERGREEN_Q.values()), validate.EVERGREEN_N)
        ev, _, _ = mocks.make_sets(self.tmp); ev[0]["category"] = "product/price"
        (self.tmp / "search_evergreen.jsonl").write_text("".join(json.dumps(r) + "\n" for r in ev))
        self.assertTrue(any("unknown category 'product/price'" in e for e in validate.validate(self.tmp, self.cfg)[0]))
    def test_ac13_ap_dropped_and_logged(self):
        _, _, log = self._gen(); drops = [l for l in log if l["event"] == "feed_dropped"]
        self.assertEqual([d["feed"] for d in drops], ["ap_top"]); self.assertIn("AP", drops[0]["reason"])

    # ---------- AC5 / AC6 / AC14 cost ----------
    def _lines(self, n, passed, units=1, proxy=0):
        return [{"status": "pass" if i < passed else "fail", "billed_units": units, "proxy_gb": proxy} for i in range(n)]
    def test_ac5_one_fixture_per_model(self):
        L = self._lines(10, 5)
        exp = {"per_request": 1000 * 10 * 0.005 / 10, "credits": 1000 * 10 * 0.008 / 10, "tier": 1000 * 10 * 20 / 5000 / 10}
        for m, v in exp.items(): self.assertAlmostEqual(cost.cost_columns(mocks.pricing_fixture(m), L)["entry_tier_cost_per_1k"], v)
        B = [{"status": "pass", "billed_units": 1.2, "proxy_gb": 0}, {"status": "fail", "billed_units": 0.1, "proxy_gb": 0}]   # -> 2 + 1 billed min
        self.assertAlmostEqual(cost.cost_columns(mocks.pricing_fixture("per_hour_browser"), B)["entry_tier_cost_per_1k"], 1000 * 3 * 0.12 / 60 / 2)
        G = [{"status": "pass", "billed_units": 0.5, "proxy_gb": 0}] * 4
        self.assertAlmostEqual(cost.cost_columns(mocks.pricing_fixture("per_gb"), G)["volume_tier_cost_per_1k"], 1000 * 2 * 8 / 4)
        # tier overage
        over = cost.billed_usd(mocks.pricing_fixture("tier"), "entry", self._lines(6000, 1)); self.assertAlmostEqual(over, 20 + 1000 * 0.005)
        # proxy fees added
        self.assertAlmostEqual(cost.billed_usd(mocks.pricing_fixture("per_request"), "entry", self._lines(2, 1, proxy=0.5)), 2 * 0.005 + 1.0 * 10)
    def test_ac5_not_captured_gives_no_cost_ac14_na_in_that_column_only(self):
        pr = mocks.pricing_fixture("per_request", volume={"name": "Scale", "unit_price": "NOT CAPTURED"})
        c = cost.cost_columns(pr, self._lines(10, 5))
        self.assertIsNotNone(c["entry_tier_cost_per_1k"]); self.assertIsNone(c["volume_tier_cost_per_1k"])
        self.assertEqual(cost.fmt(c["volume_tier_cost_per_1k"]), "n/a"); self.assertNotEqual(cost.fmt(c["entry_tier_cost_per_1k"]), "n/a")
        none = cost.cost_columns({"model": "NOT CAPTURED", "tiers": {}}, self._lines(3, 1))
        self.assertIsNone(none["entry_tier_cost_per_1k"]); self.assertIsNone(none["volume_tier_cost_per_1k"])
    def test_ac6_successful_ge_attempted_and_inf(self):
        import random
        rng = random.Random(3)
        for m in ("per_request", "credits", "tier"):
            for _ in range(200):
                n = rng.randint(1, 300); p = rng.randint(0, n); c = cost.cost_columns(mocks.pricing_fixture(m), self._lines(n, p))
                for k in ("entry", "volume"):
                    self.assertGreaterEqual(c[f"{k}_tier_cost_per_successful_1k"], c[f"{k}_tier_cost_per_1k"])
                    if p == 0: self.assertEqual(c[f"{k}_tier_cost_per_successful_1k"], math.inf)
        self.assertEqual(cost.fmt(math.inf), "∞")
    def test_free_quota_never_lowers_cost(self):
        a = mocks.pricing_fixture("per_request"); b = json.loads(json.dumps(a)); b["free_tier"]["units_per_month"] = 10 ** 9
        self.assertEqual(cost.cost_columns(a, self._lines(10, 5)), {**cost.cost_columns(b, self._lines(10, 5)), "free_quota_note": a["free_tier"]["note"]})

    # ---------- AC7 freshness ----------
    def test_ac7_synthetic_timeline(self):
        run_at = dt.datetime(2026, 10, 19, 10, tzinfo=dt.timezone.utc); pub = (run_at - dt.timedelta(hours=30)).isoformat()
        rows = [{"id": f"q{i}", "headline": f"Acme recalls widget model {i} after battery fires", "check": {"type": "url_exact", "value": f"https://n.example/{i}"}, "published_at": pub} for i in range(4)]
        miss = [{"url": "https://other.example/", "title": "Unrelated story", "published_at": pub}]
        probes = {"q0": [{"offset_h": 0, "results": [{"url": "https://n.example/0"}]}],                                                     # canonical URL, lag 30
                  "q1": [{"offset_h": 0, "results": [{"url": "https://dup.example/1", "title": "Acme recalls widget model 1 over battery fires", "published_at": pub}]}],  # same event by title
                  "q2": [{"offset_h": 0, "results": miss}, {"offset_h": 6, "results": miss}, {"offset_h": 24, "results": [{"url": "https://n.example/2"}]}],          # lag 54
                  "q3": [{"offset_h": h, "results": miss} for h in (0, 6, 24, 72)]}                                                      # censored
        f = freshness.freshness(rows, probes, run_at, "2026-43", "sha")
        self.assertEqual(f["n"], 4); self.assertAlmostEqual(f["fresh_hit_at_10"], 0.5); self.assertAlmostEqual(f["median_lag_h"], 30.0)
        self.assertAlmostEqual(f["censored_share"], 0.25)
        self.assertEqual(freshness.misses_due(rows, {k: v[:1] for k, v in probes.items()}, 6), ["q2", "q3"])
    def _ev(self, headline, title, dh):
        pub = dt.datetime(2026, 10, 18, tzinfo=dt.timezone.utc)
        row = {"headline": headline, "check": {"value": "https://orig.example/a"}, "published_at": pub.isoformat()}
        return freshness.same_event(row, {"url": "https://other.example/b", "title": title, "published_at": (pub + dt.timedelta(hours=dh)).isoformat()})
    def test_same_event_jaccard_edge_049_vs_050(self):
        common = [f"w{i:03d}" for i in range(49)]
        a49 = " ".join(common + [f"a{i:02d}" for i in range(26)]); b49 = " ".join(common + [f"b{i:02d}" for i in range(25)])
        self.assertAlmostEqual(freshness.jaccard(a49, b49), 0.49); self.assertFalse(self._ev(a49, b49, 1))
        a50, b50 = "alpha beta gamma", "alpha beta delta"          # 2 / 4 = 0.5
        self.assertAlmostEqual(freshness.jaccard(a50, b50), 0.5); self.assertTrue(self._ev(a50, b50, 1))
        # stopwords are removed before scoring: adding them changes nothing
        self.assertAlmostEqual(freshness.jaccard("the alpha and beta of gamma", "a alpha beta to delta"), 0.5)
        # canonical URL match needs no title similarity
        row = {"headline": "x", "check": {"value": "https://www.Orig.example/a/"}, "published_at": "2026-10-18T00:00:00+00:00"}
        self.assertTrue(freshness.same_event(row, {"url": "http://orig.example/a"}))
    def test_same_event_window_edge_71h_vs_73h(self):
        t = "Acme recalls widget after battery fires"
        self.assertTrue(self._ev(t, t, 71)); self.assertTrue(self._ev(t, t, -71)); self.assertTrue(self._ev(t, t, 72))
        self.assertFalse(self._ev(t, t, 73)); self.assertFalse(self._ev(t, t, -73))
    def test_query_text_is_cleaned_headline_max_14_words(self):
        self.assertEqual(newsgen.make_query("Acme  recalls widget - Mock Outlet", 14), "Acme recalls widget")
        self.assertEqual(newsgen.make_query("Big news!!", 14), "Big news")
        long = " ".join(f"w{i}" for i in range(20)); self.assertEqual(len(newsgen.make_query(long, CFG["news"]["query_max_words"]).split()), 14)
        rows, _, _ = self._gen(); self.assertTrue(all(len(r["query"].split()) <= 14 for r in rows))
    def test_no_outlet_cap(self):
        log = []; cfg = json.loads(json.dumps(CFG))
        rows, _ = newsgen.generate(cfg, RUN_AT, "7", lambda f: (RSS / f"{f['id']}.xml").read_text(), log)
        from collections import Counter
        self.assertGreater(max(Counter(r["outlet"] for r in rows).values()), 3)   # e.g. single-host feeds now contribute up to 15
        m = newsgen.meta(rows, "news_2026-43.jsonl", CFG, RUN_AT, "7", log); self.assertEqual(m["caps"], {"per_feed": 15})

    # ---------- runner (AC9, AC11, AC12) ----------
    def _setup_run(self, units_per_month=1000, needs_card=False, tos_allowed="no", ap=None, providers=None):
        self.tmp = Path(tempfile.mkdtemp(prefix="run-", dir=self.tmp)); sd = self.tmp / "sets"; ev, sc, ts = mocks.make_sets(sd)
        rows, name, log = self._gen(); newsgen.write(rows, sd / name, newsgen.meta(rows, name, CFG, RUN_AT, "7", log))
        sets = {f: {"rows": read_jsonl(sd / f"{f}.jsonl"), "sha": sha256_file(sd / f"{f}.jsonl")} for f in ("search_evergreen", "scrape_urls", name[:-6])}
        sets["browser_tasks"] = {"rows": load_yaml(sd / "browser_tasks.yaml"), "sha": sha256_file(sd / "browser_tasks.yaml")}
        provs = providers or [{"id": "mocksearch", "categories": ["search"], "key_env": "MOCK_SEARCH_KEY"},
                              {"id": "mockscrape", "categories": ["scrape"], "key_env": "MOCK_SCRAPE_KEY"},
                              {"id": "mockbrowser", "categories": ["browser"], "key_env": "MOCK_BROWSER_KEY"}]
        models = {"search": "credits", "scrape": "per_request", "browser": "per_hour_browser"}
        pricing = {}
        for p in provs:
            for c in p["categories"]:
                pr = mocks.pricing_fixture(models[c]); pr["free_tier"]["units_per_month"] = units_per_month; pr["free_tier"]["needs_card"] = needs_card
                pricing.setdefault(p["id"], {})[c] = pr
        tos = {p["id"]: {"terms_url": "https://mock.example/terms", "checked_at": "MOCK", "allowed": tos_allowed} for p in provs}
        ad = mocks.MockAdapters(); adapters = {}
        for p in provs: adapters.update(ad.table(p["id"], p["categories"]))
        env = {p["key_env"]: "mock-not-a-key" for p in provs}
        return dict(providers=provs, sets=sets, pricing=pricing, tos=tos, adapters=adapters, cfg=self.cfg, runs_dir=self.tmp / "runs",
                    ledger_path=self.tmp / "spend" / "2026-10-quota.json", run_date="2026-10-19", env=env, mock=True,
                    now_fn=lambda: dt.datetime.fromisoformat("2026-10-19T11:23:00+00:00")), ad, sd

    def test_ac9_dry_run_full_table_pipeline(self):
        kw, ad, sd = self._setup_run(units_per_month=100000)
        s = runner.run(**kw); self.assertTrue(all(p["status"] == "ran" for p in s["providers"]), s)
        doc = tables.build(kw["runs_dir"], kw["pricing"], sd)
        self.assertTrue(doc["has_results"]); self.assertEqual({k for k, v in doc["tables"].items() if v}, {"search", "scrape", "browser"})
        news_row = next(r for r in doc["tables"]["search"] if r["set"].startswith("news_")); self.assertEqual(news_row["set_rows"], len(kw["sets"][news_row["set"]]["rows"]))
        self.assertTrue(doc["freshness"]); self.assertGreater(doc["freshness"][0]["median_lag_h"]["v"], 0)
        self.assertEqual(tables.untraced_cells(doc, kw["runs_dir"].parent), [])
        for t in doc["tables"].values():
            for r in t:
                self.assertTrue(r["mock"])
                for k in ("entry", "volume"):
                    self.assertIn(f"{k}_tier_cost_per_1k", r); self.assertIn(f"{k}_tier_cost_per_successful_1k", r)
                self.assertTrue(r["free_quota_note"])                     # AC14 quota note present
    def test_ac8_untraced_cell_fails(self):
        kw, _, sd = self._setup_run(units_per_month=100000); runner.run(**kw); doc = tables.build(kw["runs_dir"], kw["pricing"], sd)
        doc["tables"]["search"][0]["success_rate"]["src"] = []
        self.assertTrue(tables.untraced_cells(doc, kw["runs_dir"].parent))
        doc2 = tables.build(kw["runs_dir"], kw["pricing"], sd); doc2["tables"]["scrape"][0]["p50_latency_ms"]["src"][0]["lines"] = [99999]
        self.assertTrue(tables.untraced_cells(doc2, kw["runs_dir"].parent))
    def test_ac11_halts_before_quota_and_zero_billed(self):
        kw, ad, _ = self._setup_run(units_per_month=21)       # search: 2 credits/call -> 10 calls then halt (next would hit 22)
        s = runner.run(**kw); sp = next(p for p in s["providers"] if p["provider"] == "mocksearch")
        self.assertEqual(sp["status"], "stale_quota"); self.assertEqual(sp["calls"], 10); self.assertLessEqual(sp["quota_used"], 21)
        br = next(p for p in s["providers"] if p["provider"] == "mockbrowser")
        self.assertEqual(br["status"], "stale_quota"); self.assertLessEqual(br["quota_used"], 21)   # 120s timeout -> 2 min estimate
        led = json.loads(kw["ledger_path"].read_text()); self.assertEqual(led["over_free_units"], 0)
        # a second run the same month resumes from the ledger and halts immediately
        s2 = runner.run(**kw); self.assertEqual(next(p for p in s2["providers"] if p["provider"] == "mocksearch")["calls"], 0)
    def test_ac11_billed_over_quota_fails(self):
        kw, ad, _ = self._setup_run(units_per_month=5)
        bad = mocks.MockAdapters(units=50); kw["adapters"]["mocksearch"] = {"search": bad.search}   # vendor bills more than estimated
        with self.assertRaises(runner.CapViolation): runner.run(**kw)
    def test_ac11_nonzero_cap_refused(self):
        kw, _, _ = self._setup_run(); kw["env"] = dict(kw["env"], MONTHLY_CAP_USD="5")
        with self.assertRaises(runner.CapViolation): runner.run(**kw)
    def test_ac11_no_free_tier_or_card_skipped_unless_ceo_listed(self):
        for upm, card, ceo, want in ((None, False, [], "skipped_no_free_tier"), ("NOT CAPTURED", False, [], "skipped_no_free_tier"),
                                     (1000, True, [], "skipped_no_free_tier"), (1000, None, [], "skipped_no_free_tier"),
                                     (1000, True, ["mocksearch"], "ran")):
            self.setUp(); kw, _, _ = self._setup_run(units_per_month=upm, needs_card=card, providers=[{"id": "mocksearch", "categories": ["search"], "key_env": "K"}])
            kw["cfg"] = dict(kw["cfg"], ceo_free_tier_allowlist=ceo)
            if want == "ran": kw["pricing"]["mocksearch"]["search"]["free_tier"]["units_per_month"] = 100000
            self.assertEqual(runner.run(**kw)["providers"][0]["status"], want, (upm, card, ceo))
    def test_missing_key_and_adapter(self):
        kw, _, _ = self._setup_run(); kw["env"] = {}; self.assertTrue(all(p["status"] == "skipped_no_key" for p in runner.run(**kw)["providers"]))
        kw, _, _ = self._setup_run(); kw["adapters"] = {}; self.assertTrue(all(p["status"] == "not_implemented" for p in runner.run(**kw)["providers"]))
    def test_no_key_reason_hides_env_name(self):
        kw, _, _ = self._setup_run(); kw["env"] = {}
        for p in runner.run(**kw)["providers"]: self.assertEqual(p["reason"], "not run (no key)")
    def test_missing_set_reason_has_no_path(self):
        with tempfile.TemporaryDirectory() as td:
            out = validate.validate_by_set(td, {"task_site_allowlist": []})
            for name in ("search_evergreen", "scrape_urls", "browser_tasks"):
                self.assertTrue(out[name][0], name)
                for e in out[name][0]: self.assertNotIn("/", e); self.assertNotIn(td, e)
    def test_published_benchmark_json_has_no_paths_or_env_names(self):
        import re
        txt = (SITE / "data" / "benchmarks" / "latest.json").read_text()
        self.assertIsNone(re.search(r"/(workspace|home|Users|root|tmp)/", txt))
        self.assertIsNone(re.search(r"[A-Z][A-Z0-9_]*_API_(KEY|TOKEN)", txt))
    def test_ac12_skipped_tos_never_a_failure(self):
        kw, _, sd = self._setup_run(units_per_month=100000, tos_allowed="no"); runner.run(**kw); doc = tables.build(kw["runs_dir"], kw["pricing"], sd)
        r = doc["tables"]["scrape"][0]; self.assertEqual(r["skipped_tos"]["count"], 30)
        self.assertEqual(r["attempted"]["v"], 120); self.assertEqual(r["passed"]["v"] + r["failed"]["v"], 120)
        self.assertIn("tos_matrix: allowed=no", r["skipped_tos"]["tos_reason"]); self.assertIn("mock.example/terms", r["skipped_tos"]["tos_reason"])
        lines = read_jsonl(kw["runs_dir"] / "2026-10-19" / "mockscrape" / "scrape_urls.jsonl")
        self.assertTrue(all(l["pass"] is None and l["billed_units"] == 0 for l in lines if l["status"] == "skipped_tos"))
        # denominators exclude skipped_tos: cost per 1k uses 120 attempted
        self.setUp(); kw2, _, sd2 = self._setup_run(units_per_month=100000, tos_allowed="yes"); runner.run(**kw2)
        self.assertEqual(tables.build(kw2["runs_dir"], kw2["pricing"], sd2)["tables"]["scrape"][0]["attempted"]["v"], 150)

    # ---------- mocks never published ----------
    def test_mock_output_refused_in_published_dirs(self):
        kw, _, _ = self._setup_run()
        for bad in (SITE / "data" / "x", BENCH / "runs"):
            k = dict(kw, runs_dir=bad)
            with self.assertRaises(RuntimeError): runner.run(**k)
        doc_out = SITE / "data" / "benchmarks" / "mock-should-not-exist.json"
        kw2, _, sd = self._setup_run(units_per_month=100000); runner.run(**kw2)
        pr = self.tmp / "pricing.yaml"; pr.write_text(json.dumps({"providers": kw2["pricing"]}))
        self.assertEqual(tables.main([str(kw2["runs_dir"]), str(doc_out), "--pricing", str(pr), "--sets", str(sd)]), 2)
        self.assertFalse(doc_out.exists())

    # ---------- AC10 secrets ----------
    def test_ac10_repo_clean_and_scanner_detects(self):
        self.assertEqual(secrets_scan.scan(SITE), [])
        d = self.tmp / "leak"; d.mkdir(); (d / "x.py").write_text("EXA_API_KEY = '" + "a1B2c3D4" * 4 + "'\n" + "tok = 'gh" + "p_" + "Z" * 36 + "'\n")
        self.assertEqual(len(secrets_scan.scan(d)), 2)

    # ---------- checks ----------
    def test_scrape_challenge_and_selector(self):
        row = {"marker": {"type": "selector_text", "value": "div.content::hello world"}, "min_chars": 5}
        self.assertTrue(checks.scrape_check(row, {"text": "hello world here", "html": '<div class="content">Hello, world!</div>'}, [])[0])
        self.assertFalse(checks.scrape_check(row, {"text": "Just a moment... hello world", "html": '<div class="content">hello world</div>'}, CFG["challenge_signatures"])[0])

    # ---------- CEO decisions, Oct 15 (verified files, read-only) ----------
    def _real(self):
        return (load_yaml(BENCH / "pricing.yaml")["providers"], load_yaml(BENCH / "tos_matrix.yaml")["providers"],
                json.loads((SITE / "collectors/providers.json").read_text())["providers"])
    def _real_run(self, provs=None, cfg=None, pricing=None):
        rp, tos, allp = self._real(); pricing = pricing or rp; provs = provs or allp
        sd = self.tmp / "rsets"; mocks.make_sets(sd)
        tasks = load_yaml(sd / "browser_tasks.yaml"); tasks[0] = dict(tasks[0], category="anti_bot")   # an anti-bot browser task for decision 2
        sets = {f: {"rows": read_jsonl(sd / f"{f}.jsonl"), "sha": "x"} for f in ("search_evergreen", "scrape_urls")}
        sets["browser_tasks"] = {"rows": tasks, "sha": "x"}
        ad = mocks.MockAdapters(); adapters = {}
        for p in provs: adapters.update(ad.table(p["id"], p["categories"]))
        return runner.run(providers=provs, sets=sets, pricing=pricing, tos=tos, adapters=adapters, cfg=cfg or CFG, runs_dir=self.tmp / "rruns",
                          ledger_path=self.tmp / "rspend.json", run_date="2026-10-19", env={p["key_env"]: "mock-not-a-key" for p in provs}, mock=True,
                          now_fn=lambda: dt.datetime.fromisoformat("2026-10-19T11:23:00+00:00")), pricing, tos
    def test_d1_brave_excluded_from_runner_and_tables(self):
        s, pricing, tos = self._real_run()
        b = [p for p in s["providers"] if p["provider"] == "brave"]
        self.assertEqual([p["status"] for p in b], ["excluded_tos_benchmarking"]); self.assertIn("benchmark", b[0]["reason"]); self.assertIn("(xiii)", b[0]["reason"])
        self.assertFalse((self.tmp / "rruns" / "2026-10-19" / "brave").exists())
        # even a stray brave log never reaches a table
        f = self.tmp / "rruns" / "2026-10-19" / "brave" / "search_evergreen.jsonl"; f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"ts": "2026-10-19T11:23:00+00:00", "status": "pass", "set_sha": "x", "latency_ms": 1, "billed_units": 1, "mock": True}) + "\n")
        doc = tables.build(self.tmp / "rruns", pricing, None, tos)
        self.assertNotIn("brave", [r["provider"] for t in doc["tables"].values() for r in t])
        self.assertEqual([e["provider"] for e in doc["exclusions"]], ["brave"]); self.assertIn("brave.com", doc["exclusions"][0]["terms_url"])
    def test_d2_steel_browserless_skip_anti_bot(self):
        cfg = dict(CFG, month1_free_tier_providers=None, ceo_free_tier_allowlist=["steel", "browserless"])
        pricing, tos, allp = self._real()
        provs = [p for p in allp if p["id"] in ("steel", "browserless")]
        pricing["steel"]["browser"]["free_tier"]["units_per_month"] = 100000   # in-memory only: steel has no monthly quota, needed to reach the ToS check
        for pid in ("steel", "browserless"):
            self.assertEqual(tos[pid]["allowed"], "no"); self.assertEqual(tos[pid]["anti_bot_scraping"]["status"], "forbidden")
            self.assertFalse(runner.anti_bot_allowed(tos[pid]))
        s, _, _ = self._real_run(provs, cfg, pricing)
        for pid in ("steel", "browserless"):
            lines = read_jsonl(self.tmp / "rruns" / "2026-10-19" / pid / "browser_tasks.jsonl")
            ab = [l for l in lines if l["item_id"] == "bt-01"]
            self.assertEqual(ab[0]["status"], "skipped_tos"); self.assertIn("anti_bot_scraping=forbidden", ab[0]["reason"])
            self.assertTrue(all(l["status"] != "skipped_tos" for l in lines if l["item_id"] != "bt-01"))
        # a provider with allowed=yes but anti_bot_scraping forbidden is still skipped
        self.assertFalse(runner.anti_bot_allowed({"allowed": "yes", "anti_bot_scraping": {"status": "forbidden"}}))
        self.assertTrue(runner.anti_bot_allowed({"allowed": "yes", "anti_bot_scraping": {"status": "unclear"}}))
    def test_d3_demo_allowlist_and_webscraper_paths(self):
        self.assertEqual(CFG["task_site_allowlist"], ["the-internet.herokuapp.com", "books.toscrape.com", "quotes.toscrape.com", "practicetestautomation.com", "webscraper.io"])
        ev, sc, ts = mocks.make_sets(self.tmp)
        urls = {"https://the-internet.herokuapp.com/login": True, "https://books.toscrape.com/": True, "https://quotes.toscrape.com/js/": True,
                "https://practicetestautomation.com/practice-test-login/": True, "https://webscraper.io/test-sites/e-commerce/allinone": True,
                "https://demoqa.com/text-box": False, "https://webscraper.io/test-sites/pagination/": False, "https://webscraper.io/test-sites/pagination-x?page=2": False,
                "https://webscraper.io/test-sites/scroll/": False, "https://webscraper.io/test-sites/load-more/": False,
                "https://webscraper.io/test-sites/product/1": False, "https://webscraper.io/pricing": False}
        for u, ok in urls.items():
            t2 = [dict(t, start_url=u) for t in ts]; (self.tmp / "browser_tasks.yaml").write_text(json.dumps(t2))
            errs = validate.validate_by_set(self.tmp, CFG)["browser_tasks"][0]
            self.assertEqual(errs == [], ok, (u, errs[:1]))
    def test_d4_month1_only_six_providers(self):
        s, pricing, tos = self._real_run()
        st = {(p["provider"], p["category"]): p["status"] for p in s["providers"]}
        ran = {k for k, v in st.items() if v in ("ran", "stale_quota")}
        self.assertEqual({k[0] for k in ran}, {"exa", "tavily", "firecrawl", "brightdata", "apify", "browserless"})
        for (pid, cat), v in st.items():
            if pid == "brave": self.assertEqual(v, "excluded_tos_benchmarking")
            elif pid not in CFG["month1_free_tier_providers"]: self.assertEqual(v, "skipped_not_month1", pid)
        self.assertTrue(all(p.get("reason") for p in s["providers"] if p["status"].startswith(("skipped", "excluded"))))
        # quotas from the verified pricing.yaml are respected: nothing exceeds its free monthly quota
        led = json.loads((self.tmp / "rspend.json").read_text()); self.assertEqual(led["over_free_units"], 0)
        self.assertEqual(st[("apify", "scrape")], "stale_quota"); self.assertLessEqual(led["used"]["apify"], 25)
        ap = next(p for p in s["providers"] if p["provider"] == "apify" and p["category"] == "scrape"); self.assertEqual(ap["calls"], 12)      # #4 worst case 2 CU/call
        self.assertEqual(led["used"]["tavily"], 200)                                                            # tavily guarded at 2/call
        self.assertLessEqual(led["used"]["browserless"], 1000); self.assertLessEqual(led["used"]["exa"], 1428)
        fc = read_jsonl(self.tmp / "rruns" / "2026-10-19" / "firecrawl" / "search_evergreen.jsonl")
        self.assertTrue(all(l["billed_units"] == 2 for l in fc))               # Firecrawl search = 2 credits/call
        bl = read_jsonl(self.tmp / "rruns" / "2026-10-19" / "browserless" / "browser_tasks.jsonl")
        self.assertTrue(all(l["billed_units"] == 3 for l in bl if l["status"] != "skipped_tos"))   # 1.5 min = 3 x 30 s units
    def test_real_pricing_costs_compute_or_na(self):
        pricing, _, _ = self._real(); n = 0
        for pid, cats in pricing.items():
            for c, e in cats.items():
                n += 1; r = cost.cost_columns(e, [{"status": "pass", "billed_units": 1.0, "proxy_gb": 0}] * 4)
                self.assertIn("free_quota_note", r)
        self.assertEqual(n, 20)   # 19 + apify search (RAG Web Browser, added 2026-10-19)
        self.assertIsNone(cost.cost_columns(pricing["browserbase"]["browser"], [{"status": "pass", "billed_units": 1.0, "proxy_gb": 0}])["entry_tier_cost_per_1k"])

    # ---------- CEO final changes ----------
    def test_7_providers_json_has_no_quota_or_cost_fields(self):
        provs = json.loads((SITE / "collectors/providers.json").read_text())["providers"]
        banned = {"free_quota", "entry_cost", "costs", "cost", "pricing", "free_tier", "quota", "units_per_month", "unit_price", "tiers"}
        for p in provs:
            self.assertEqual(set(p) & banned, set(), p["id"]); self.assertEqual(set(p), {"id", "name", "categories", "pricing_url", "key_env"}, p["id"])
    def test_4_worst_case_guard_is_data_and_halts_before_call(self):
        wc = CFG["worst_case_units_per_call"]
        self.assertEqual({k: v for k, v in wc.items()}.keys(), {"tavily", "apify"})
        self.assertTrue(all(v["verified"] is False for v in wc.values()))
        src = (BENCH / "awibench" / "runner.py").read_text(); self.assertNotIn('"apify"', src); self.assertNotIn('"tavily"', src)
        pricing, tos, allp = self._real()
        self.assertEqual(pricing["apify"]["scrape"]["free_tier"]["units_per_month"], 25)
        cfg = dict(CFG, month1_free_tier_providers=["apify"])
        calls = []
        class Spy(mocks.MockAdapters):
            def scrape(self, key, item):
                calls.append(item["id"]); out = super().scrape(key, item); out["billed_units"] = 2; return out
        sd = self.tmp / "ws"; mocks.make_sets(sd)
        sets = {"scrape_urls": {"rows": read_jsonl(sd / "scrape_urls.jsonl"), "sha": "x"}}
        s = runner.run(providers=[p for p in allp if p["id"] == "apify"], sets=sets, pricing=pricing, tos=tos, adapters={"apify": {"scrape": Spy().scrape}},
                       cfg=cfg, runs_dir=self.tmp / "wr", ledger_path=self.tmp / "wl.json", run_date="2026-10-19", env={"APIFY_API_TOKEN": "mock-not-a-key"}, mock=True)
        ap = next(p for p in s["providers"] if p["category"] == "scrape")
        self.assertEqual(ap["status"], "stale_quota"); self.assertEqual(ap["calls"], 12); self.assertEqual(len(calls), 12)
        self.assertEqual(ap["quota_used"], 24); self.assertIn("~2 units would exceed free quota 25", ap["reason"])
        self.assertEqual(json.loads((self.tmp / "wl.json").read_text())["over_free_units"], 0)
        # same data with the guard removed would allow 25 one-unit estimates: proves the halt comes from config, not code
        s2 = runner.run(providers=[p for p in allp if p["id"] == "apify"], sets=sets, pricing=pricing, tos=tos, adapters={"apify": {"scrape": mocks.MockAdapters(units=1).scrape}},
                        cfg=dict(cfg, worst_case_units_per_call={}), runs_dir=self.tmp / "wr2", ledger_path=self.tmp / "wl2.json", run_date="2026-10-19",
                        env={"APIFY_API_TOKEN": "x"}, mock=True)
        self.assertEqual(next(p for p in s2["providers"] if p["category"] == "scrape")["calls"], 25)
    def test_1_lower_bound_driven_by_missing_fields(self):
        pricing, _, _ = self._real(); L = [{"status": "pass", "billed_units": 1.0, "proxy_gb": 0}] * 4
        for pid in ("steel", "hyperbrowser"):
            c = cost.cost_columns(pricing[pid]["browser"], L); self.assertTrue(c["entry_tier_lower_bound"], pid)
            self.assertTrue(cost.fmt(c["entry_tier_cost_per_1k"], True).startswith("≥ $"))
        for pid in ("browserless",):
            self.assertFalse(cost.cost_columns(pricing[pid]["browser"], L)["entry_tier_lower_bound"])
        # driven by fields, not ids: same provider with min/rounding filled is exact; fixture without them is a lower bound
        st = json.loads(json.dumps(pricing["steel"]["browser"])); st["tiers"]["entry"].update(min_minutes=1, rounding_minutes=1)
        self.assertFalse(cost.cost_columns(st, L)["entry_tier_lower_bound"])
        fx = mocks.pricing_fixture("per_hour_browser"); del fx["tiers"]["entry"]["rounding_minutes"]
        self.assertTrue(cost.cost_columns(fx, L)["entry_tier_lower_bound"])
        self.assertNotIn("steel", (BENCH / "awibench" / "cost.py").read_text()); self.assertNotIn("hyperbrowser", (BENCH / "awibench" / "cost.py").read_text())
        # reaches tables + display
        kw, _, sd = self._setup_run(units_per_month=100000, providers=[{"id": "mockbrowser", "categories": ["browser"], "key_env": "K"}])
        del kw["pricing"]["mockbrowser"]["browser"]["tiers"]["entry"]["min_minutes"]; runner.run(**kw)
        r = tables.build(kw["runs_dir"], kw["pricing"], sd)["tables"]["browser"][0]
        self.assertTrue(r["entry_tier_cost_per_1k"]["lower_bound"]); self.assertIn("lower bound", r["entry_tier_cost_per_1k"]["display"])
        self.assertFalse(r["volume_tier_cost_per_1k"]["lower_bound"]); self.assertTrue(r["entry_tier_cost_per_1k"]["lower_bound_reason"])

    # ---------- spec update 21:12 + Browserbase check ----------
    def test_week_flags_spec_ac3(self):
        nc = CFG["news"]
        self.assertEqual(newsgen.week_flags(50, nc), []); self.assertEqual(newsgen.week_flags(49, nc), ["short week (n=49)"])
        self.assertEqual(newsgen.week_flags(25, nc), ["short week (n=25)"])
        self.assertEqual(newsgen.week_flags(24, nc), ["short week (n=24)", "below minimum (n=24)"])
        self.assertEqual(newsgen.week_flags(0, nc), ["no data (n=0)"])
    def test_n0_week_publishes_with_no_scores(self):
        sd = self._news_dir([], "zero"); m = json.loads((sd / "news_2026-43.meta.json").read_text())
        self.assertEqual(m["flags"], ["no data (n=0)"]); self.assertEqual(validate.validate_by_set(sd, self.cfg)["news_2026-43"][0], [])
        kw, _, _ = self._setup_run(units_per_month=100000, providers=[{"id": "mocksearch", "categories": ["search"], "key_env": "K"}])
        kw["sets"] = {"news_2026-43": {"rows": [], "sha": "x", "flags": m["flags"]}}; runner.run(**kw)
        doc = tables.build(kw["runs_dir"], kw["pricing"], sd)
        self.assertFalse(doc["has_results"]); self.assertEqual(doc["freshness"], [])
    def test_browserbase_min_rounding_not_published_path(self):
        pricing, _, _ = self._real(); bb = pricing["browserbase"]["browser"]
        self.assertEqual(bb["tiers"]["entry"].get("min_minutes"), "NOT CAPTURED"); self.assertEqual(bb["tiers"]["entry"].get("rounding_minutes"), "NOT CAPTURED")
        c = cost.cost_columns(bb, [{"status": "pass", "billed_units": 1.0, "proxy_gb": 0}])
        self.assertIsNone(c["entry_tier_cost_per_1k"]); self.assertEqual(c["entry_tier_na_reason"], "lower bound n/a (not published)")
        kw, _, sd = self._setup_run(units_per_month=100000, providers=[{"id": "mockbrowser", "categories": ["browser"], "key_env": "K"}])
        kw["pricing"]["mockbrowser"]["browser"]["tiers"]["entry"].update(min_minutes="NOT CAPTURED", rounding_minutes="NOT CAPTURED"); runner.run(**kw)
        cell = tables.build(kw["runs_dir"], kw["pricing"], sd)["tables"]["browser"][0]["entry_tier_cost_per_1k"]
        self.assertIsNone(cell["v"]); self.assertEqual(cell["display"], "lower bound n/a (not published)")
    def test_browserbase_min_rounding_published_path(self):
        pricing, _, _ = self._real(); bb = json.loads(json.dumps(pricing["browserbase"]["browser"]))
        bb["tiers"]["entry"].update(min_minutes=1, rounding_minutes=1)          # hypothetical published values (in memory only)
        L = [{"status": "pass", "billed_units": 0.5, "proxy_gb": 0}, {"status": "fail", "billed_units": 1.2, "proxy_gb": 0}]   # -> 1 + 2 min
        c = cost.cost_columns(bb, L)
        self.assertAlmostEqual(c["entry_tier_cost_per_1k"], 1000 * 3 * 0.12 / 60 / 2); self.assertFalse(c["entry_tier_lower_bound"]); self.assertIsNone(c["entry_tier_na_reason"])
        del bb["tiers"]["entry"]["rounding_minutes"]
        self.assertTrue(cost.cost_columns(bb, L)["entry_tier_lower_bound"])    # min only -> lower bound under existing rules
    def test_evergreen_quotas_pinned_to_spec(self):
        spec = {"facts/reference": 30, "docs/API lookup": 25, "local/entity": 10, "academic": 10, "long-tail": 15, "non-English": 10}
        self.assertEqual(validate.EVERGREEN_Q, spec); self.assertEqual(sum(spec.values()), 100); self.assertEqual(validate.EVERGREEN_N, 100)
        self.assertEqual(mocks.EVERGREEN_Q, spec)

BENCH_SETS = SITE / "bench" / "sets"
class OwnerRulingsStage2(unittest.TestCase):
    """Owner rulings 2026-10-15 stage 2: below-minimum label, generic 429 rate_limited bucket, 'not run yet'."""
    def _log(self, d, pid, recs):
        f = Path(d) / "runs" / "2026-10-15" / pid / "news_2099-01.jsonl"; f.parent.mkdir(parents=True)
        base = {"ts": "2026-10-15T00:00:00+00:00", "set": "news_2099-01", "set_sha": "x", "set_rows": len(recs), "offset_h": 0, "mock": True, "latency_ms": 1, "billed_units": 1}
        f.write_text("".join(json.dumps(base | {"item_id": f"i{n}"} | r) + "\n" for n, r in enumerate(recs))); return Path(d) / "runs"

    def test_week_labels_below_minimum(self):
        self.assertEqual(tables.week_labels(16), ["short week (n=16)", "below minimum (n=16)"])
        self.assertEqual(tables.week_labels(30), ["short week (n=30)"]); self.assertEqual(tables.week_labels(50), [])
        self.assertEqual(tables.week_labels(0), ["no data (n=0)"])

    def test_429_rate_limited_bucket_any_provider(self):
        for pid in ("firecrawl", "someprovider"):
            with tempfile.TemporaryDirectory() as d:
                recs = [{"status": "pass"}] * 5 + [{"status": "fail", "reason": "no match"}] * 5 + \
                       [{"status": "fail", "reason": 'RuntimeError: HTTP 429: {"error":"Rate limit"}'}] * 5 + [{"status": "fail", "http_status": 429, "reason": "x"}]
                doc = tables.build(self._log(d, pid, recs), {}, None, {})
                r = doc["tables"]["search"][0]
                self.assertEqual((r["attempted"]["v"], r["passed"]["v"], r["failed"]["v"], r["rate_limited"]["v"]), (10, 5, 5, 6))
                self.assertEqual(r["success_rate"]["v"], 0.5)
                self.assertEqual(r["answered_display"], "5/10 answered + 6 rate-limited (free tier)")
                self.assertIn("free-tier rate limit is the cause", r["rate_limited"]["note"])
                self.assertIn("below minimum (n=16)", r["flags"])
                self.assertEqual(tables.untraced_cells(doc, Path(d)), [])

    def test_no_429_has_no_rate_limited_value(self):
        with tempfile.TemporaryDirectory() as d:
            r = tables.build(self._log(d, "exa", [{"status": "pass"}, {"status": "fail", "reason": "HTTP 500"}]), {}, None, {})["tables"]["search"][0]
            self.assertIsNone(r["rate_limited"]["v"]); self.assertEqual(r["answered_display"], "1/2 answered")

    def test_frozen_meta_legacy_below_min_label_still_valid(self):
        self.assertEqual(validate.legacy_flag("below minimum (n=16 < 25)"), "below minimum (n=16)")
        self.assertEqual(validate.legacy_flag("short week (n=16)"), "short week (n=16)")
        errs, _ = validate.validate_by_set(BENCH_SETS, load_yaml(SITE / "bench" / "config.yaml"))["news_2026-42"]
        self.assertEqual(errs, [])

    def test_not_run_yet(self):
        cfg = {"month1_free_tier_providers": ["exa", "apify", "browserless", "brightdata"]}
        summ = {"providers": [{"provider": "exa", "category": "search", "status": "ran"}, {"provider": "exa", "category": "scrape", "status": "not_implemented"},
                              {"provider": "apify", "category": "scrape", "status": "not_implemented"}, {"provider": "brightdata", "category": "scrape", "status": "not_implemented"},
                              {"provider": "browserless", "category": "browser", "status": "not_implemented"}, {"provider": "jina", "category": "scrape", "status": "skipped_not_month1"}]}
        doc = {"tables": {"search": [{"provider": "exa"}], "scrape": [], "browser": []}}
        nr = tables.not_run_yet(summ, cfg, doc)
        self.assertEqual([n["provider"] for n in nr], ["apify", "brightdata", "browserless"])
        self.assertTrue(all(n["status"] == "not run yet" for n in nr))

class Run1Published(unittest.TestCase):
    """Run 1 publication (owner rulings): Firecrawl 429s split from quality fails; below-minimum flag on every week-42 row;
    every published count equals a recount of the raw run log; later collectors (Apify, Browserless) on the same frozen set."""
    DOC = json.loads((SITE / "data" / "benchmarks" / "latest.json").read_text())
    def _row(self, cat, pid):
        return next(r for r in self.DOC["tables"][cat] if r["provider"] == pid)

    def test_firecrawl_429_split_in_published_table(self):
        r = self._row("search", "firecrawl")
        self.assertEqual((r["passed"]["v"], r["attempted"]["v"], r["rate_limited"]["v"]), (5, 10, 6))
        self.assertEqual(r["answered_display"], "5/10 answered + 6 rate-limited (free tier)")
        self.assertEqual(r["rate_limited"]["label"], "rate-limited (free tier)")
        for pid in ("exa", "tavily"): self.assertIsNone(self._row("search", pid)["rate_limited"]["v"])

    def test_run1_counts_match_raw_logs(self):
        want = {"exa": (12, 16, 0), "tavily": (8, 16, 0), "firecrawl": (5, 10, 6)}
        for pid, (pas, att, rl) in want.items():
            recs = read_jsonl(SITE / "bench" / "runs" / "2026-10-15" / pid / "news_2026-42.jsonl")
            n429 = sum(tables.is_rate_limited(x) for x in recs)
            scored = [x for x in recs if not tables.is_rate_limited(x)]
            self.assertEqual((sum(x["status"] == "pass" for x in scored), len(scored), n429), (pas, att, rl), pid)
            r = self._row("search", pid); self.assertEqual((r["passed"]["v"], r["attempted"]["v"]), (pas, att))
            self.assertEqual(r["run_date"], "2026-10-15")

    def test_below_minimum_flag_on_every_week42_row(self):
        rows = [r for t in self.DOC["tables"].values() for r in t] + self.DOC["freshness"]
        self.assertTrue(rows)
        for r in rows:
            self.assertIn("below minimum (n=16)", r["flags"], r["provider"]); self.assertIn("short week (n=16)", r["flags"])
        self.assertIn({"set": "news_2026-42", "flag": "below minimum (n=16)"}, self.DOC["flags"])

    def test_published_doc_fully_traced_and_brave_absent(self):
        self.assertEqual(tables.untraced_cells(self.DOC, SITE / "bench"), [])
        self.assertNotIn("brave", {r["provider"] for t in self.DOC["tables"].values() for r in t})

    def test_not_run_yet_only_for_providers_without_rows(self):
        shown = {r["provider"] for t in self.DOC["tables"].values() for r in t}
        for n in self.DOC.get("not_run_yet", []): self.assertNotIn(n["provider"], shown)
        self.assertIn("brightdata", {n["provider"] for n in self.DOC.get("not_run_yet", [])})

class Collectors2(unittest.TestCase):
    def test_newsfetch_tasks_from_frozen_set(self):
        from awibench import newsfetch
        f = BENCH_SETS / "news_2026-42.jsonl"; name, s = newsfetch.from_news_file(f)
        rows = read_jsonl(f)
        self.assertEqual(name, "newsfetch_2026-42"); self.assertEqual(s["sha"], sha256_file(f)); self.assertEqual(len(s["rows"]), len(rows))
        self.assertEqual(runner.set_kind(name), "browser"); self.assertEqual(runner.set_kind("news_2026-42"), "search")
        t = s["rows"][0]; self.assertEqual(t["start_url"], rows[0]["check"]["value"]); self.assertEqual(t["pass"], {"type": "dom_text_contains", "value": rows[0]["headline"]})
        ok, _ = checks.browser_check(t, {"dom_text": "x " + rows[0]["headline"].upper() + " y"}); self.assertTrue(ok)
        ok, _ = checks.browser_check(t, {"dom_text": "Just a moment..."}); self.assertFalse(ok)

    def test_apify_adapter_uses_fetched_url_and_bills_events(self):
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        fake = [{"searchResult": {"rank": 2, "url": "http://www.google.com/goto?url=x", "title": "B"}, "metadata": {"url": "https://b.example/2"}, "crawl": {"requestStatus": "handled"}},
                {"searchResult": {"rank": 1, "url": "http://www.google.com/goto?url=y", "title": "A"}, "metadata": {"url": "https://a.example/1"}, "crawl": {"requestStatus": "handled"}},
                {"searchResult": {"rank": 3, "url": "http://www.google.com/goto?url=z", "title": "C"}, "crawl": {"requestStatus": "failed"}}]
        old = B._get_json; B._get_json = lambda url, headers, timeout=150: (200, fake)
        try: out = B._apify_search("k", {"query": "q"})
        finally: B._get_json = old
        self.assertEqual([r["url"] for r in out["results"]][:2], ["https://a.example/1", "https://b.example/2"])
        self.assertEqual(out["fetched_pages"], 2); self.assertAlmostEqual(out["billed_units"], 100 * (0.00348 + 2 * 0.00228), places=4)

    def test_late_run_gets_no_freshness_row(self):
        with tempfile.TemporaryDirectory() as d:
            sd = Path(d) / "sets"; sd.mkdir(); rows = read_jsonl(BENCH_SETS / "news_2026-42.jsonl")
            (sd / "news_2026-42.jsonl").write_text((BENCH_SETS / "news_2026-42.jsonl").read_text())
            (sd / "news_2026-42.meta.json").write_text(json.dumps({"run_at": "2026-10-15T21:00:00-04:00"}))
            for day in ("2026-10-15", "2026-10-19"):
                f = Path(d) / "runs" / day / f"p{day[-2:]}" / "news_2026-42.jsonl"; f.parent.mkdir(parents=True)
                f.write_text("".join(json.dumps({"ts": f"{day}T12:00:00+00:00", "set": "news_2026-42", "set_sha": "x", "set_rows": 16, "offset_h": 0, "mock": False,
                    "latency_ms": 1, "billed_units": 1, "item_id": r["id"], "status": "fail", "results": []}) + "\n" for r in rows))
            doc = tables.build(Path(d) / "runs", {}, sd, {})
            self.assertEqual([f["provider"] for f in doc["freshness"]], ["p15"])
            self.assertEqual([n["provider"] for n in doc["freshness_notes"]], ["p19"])

    def test_newsgen_more_urls_and_cross_feed_dedupe(self):
        def item(t, link, h): return f"<item><title>{t}</title><link>{link}</link><pubDate>{(RUN_AT - dt.timedelta(hours=h)).strftime('%a, %d %b %Y %H:%M:%S %z')}</pubDate></item>"
        feeds = {("a", 0): "<rss><channel>" + item("One story", "https://x.example/1", 30) + "</channel></rss>",
                 ("a", 1): "<rss><channel>" + item("Older page story", "https://x.example/2", 40) + "</channel></rss>",
                 ("b", 0): "<rss><channel>" + item("One story", "https://x.example/1/", 30) + item("Other", "https://x.example/3", 50) + "</channel></rss>"}
        cfg = {"news": dict(CFG["news"]), "news_feeds": [{"id": "a", "url": "https://a/rss", "more_urls": ["https://a/rss?paged=2"], "section": "s"}, {"id": "b", "url": "https://b/rss", "section": "s"}]}
        log = []; rows, _ = newsgen.generate(cfg, RUN_AT, "1", lambda f: feeds[(f["id"], f.get("page", 0))], log)
        self.assertEqual(sorted(r["source_url"] for r in rows), ["https://x.example/1", "https://x.example/2", "https://x.example/3"])
        self.assertEqual([l["items"] for l in log if l["event"] == "feed_read"], [2, 2])

    def test_config_feeds_capped_and_more_than_week1(self):
        live = [f for f in CFG["news_feeds"] if f.get("url")]
        self.assertGreaterEqual(len(live), 11); self.assertEqual(CFG["news"]["per_feed"], 15); self.assertEqual(CFG["news"]["total"], 50)
        self.assertEqual((CFG["news"]["min_age_h"], CFG["news"]["max_age_h"]), (24, 72))
        self.assertTrue(all(f["url"].startswith("https://") and f.get("url_verified") for f in live))

class Rulings3(unittest.TestCase):
    """CEO rulings 2026-10-19: no-key runs never overwrite published data; feeds must be RSS/Atom and robots-allowed; Browserless kept."""
    def test_no_key_run_writes_nothing(self):
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        out = SITE / "data" / "benchmarks" / "latest.json"; before = out.read_bytes()
        runs = SITE / "bench" / "runs"; snap = sorted((str(f), f.stat().st_mtime_ns) for f in runs.rglob("*") if f.is_file())
        env = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_API_TOKEN", "_TOKEN", "_KEY"))}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "argv", ["benchmark.py", "--mode", "full"]), \
             mock.patch("builtins.print") as pr:
            self.assertEqual(B.main(), 0)
        self.assertEqual(out.read_bytes(), before)
        self.assertEqual(sorted((str(f), f.stat().st_mtime_ns) for f in runs.rglob("*") if f.is_file()), snap)
        self.assertTrue(any("SKIP publish" in " ".join(map(str, c.args)) for c in pr.call_args_list))

    def test_invalid_key_401_skips_and_writes_nothing(self):
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B, urllib.error, io
        dirs = [SITE / "data" / "benchmarks", SITE / "bench" / "runs", SITE / "data" / "spend"]
        snap = lambda: sorted((str(f), hashlib.sha256(f.read_bytes()).hexdigest()) for d in dirs for f in d.rglob("*") if f.is_file())
        before = snap(); calls = []
        def deny(req, **kw): calls.append(req.full_url); raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error":"invalid api key"}'))
        env = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_API_TOKEN", "_TOKEN", "_KEY"))} | {"EXA_API_KEY": "invalid-dummy"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "argv", ["benchmark.py", "--mode", "full"]), \
             mock.patch("urllib.request.urlopen", deny), mock.patch("builtins.print") as pr:
            self.assertEqual(B.main(), 0)
        self.assertEqual(snap(), before)   # latest.json, bench/runs and data/spend byte-identical
        self.assertEqual(len(calls), 1)    # stopped at the first auth error
        self.assertTrue(any("SKIP publish" in " ".join(map(str, c.args)) for c in pr.call_args_list))
        self.assertTrue(any('"stale_auth_error"' in " ".join(map(str, c.args)) for c in pr.call_args_list))

    def _late_auth_run(self, code):
        tmp = Path(tempfile.mkdtemp(prefix="awi-test-"))
        providers = [{"id": "exa", "key_env": "EXA_API_KEY", "categories": ["search"]}, {"id": "tavily", "key_env": "TAVILY_API_KEY", "categories": ["search"]}]
        pricing = {pid: {"search": {"model": "credits", "free_tier": {"units_per_month": 1000, "needs_card": False}}} for pid in ("exa", "tavily")}
        rows = [{"id": f"q{i}", "query": f"q {i}", "check": {"type": "url_exact", "value": "https://a.example/x"}} for i in range(10)]
        n = []
        def exa(key, item):   # 5 data calls (3 pass, 2 fail), then an auth error
            n.append(1)
            if len(n) > 5: raise RuntimeError(f"HTTP {code}: invalid api key")
            return {"billed_units": 1, "results": [{"url": "https://a.example/x" if len(n) <= 3 else "https://b.example/y", "title": "x"}]}
        def tavily(key, item): raise RuntimeError("HTTP 500: upstream error")   # every call errors, none is an auth error
        led = tmp / "quota.json"; led.write_text(json.dumps({"month": "2026-10", "used": {"exa": 33, "tavily": 66}, "over_free_units": 0}))
        s = runner.run(providers=providers, sets={"search_evergreen": {"rows": rows, "sha": "x"}}, pricing=pricing, tos={},
                       adapters={"exa": {"search": exa}, "tavily": {"search": tavily}}, cfg={},
                       runs_dir=tmp / "runs", ledger_path=led, run_date="2026-10-19", env={"EXA_API_KEY": "d", "TAVILY_API_KEY": "d"}, mock=True)
        p = {x["provider"]: x for x in s["providers"]}
        self.assertEqual((p["exa"]["status"], p["exa"]["calls"], p["exa"]["data_calls"]), ("stale_auth_error", 6, 5))
        self.assertEqual((p["tavily"]["status"], p["tavily"]["calls"]), ("stale_error", 10))   # every call errored: stale, spend kept
        lines = read_jsonl(tmp / "runs" / "2026-10-19" / "exa" / "search_evergreen.jsonl")
        self.assertEqual([l["status"] for l in lines], ["pass"] * 3 + ["fail"] * 2 + ["auth_error"]); self.assertIn(f"HTTP {code}", lines[-1]["reason"])
        self.assertEqual(len(read_jsonl(tmp / "runs" / "2026-10-19" / "tavily" / "search_evergreen.jsonl")), 10)   # stale run log still written
        self.assertEqual(tables.build(tmp / "runs", pricing)["tables"]["search"], [])   # a log with an auth error is never published
        return json.loads(led.read_text())["used"]

    def test_late_401_keeps_data_rows_and_spend(self):
        # pre 33 + 5 billed exa calls, the 401 bills 0 (assumption); the 10 errored tavily calls stay billed at the estimate (CEO: spend always logged)
        self.assertEqual(self._late_auth_run(401), {"exa": 38, "tavily": 76})

    def test_late_403_is_charged_the_estimate(self):   # a 403 may mean quota used up, so it is charged
        self.assertEqual(self._late_auth_run(403), {"exa": 39, "tavily": 76})

    # ---------- CEO ruling 2026-10-19: any 401/403 -> stale_auth_error, previous row kept ----------
    PROV = [{"id": "exa", "key_env": "EXA_API_KEY", "categories": ["search"]}]
    PRICING = {"exa": {"search": {"model": "credits", "free_tier": {"units_per_month": 1000, "needs_card": False}}}}
    ROWS = [{"id": f"q{i}", "query": f"q {i}", "check": {"type": "url_exact", "value": "https://a.example/x"}} for i in range(8)]

    def _guard_run(self, tmp, day, fail_at=None, code=401, n_rows=8):
        n = []
        def exa(key, item):
            n.append(1)
            if fail_at is not None and len(n) == fail_at: raise RuntimeError(f"HTTP {code}: invalid api key")
            return {"billed_units": 1, "results": [{"url": "https://a.example/x" if len(n) % 2 else "https://b.example/y", "title": "x"}]}
        s = runner.run(providers=self.PROV, sets={"search_evergreen": {"rows": self.ROWS[:n_rows], "sha": "x"}}, pricing=self.PRICING, tos={},
                       adapters={"exa": {"search": exa}}, cfg={}, runs_dir=tmp / "runs", ledger_path=tmp / "quota.json",
                       run_date=day, env={"EXA_API_KEY": "d"}, mock=True)
        return s, len(n)

    def test_late_401_previous_row_byte_identical(self):
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        tmp = Path(tempfile.mkdtemp(prefix="awi-test-"))
        self._guard_run(tmp, "2026-10-12")
        prev = tables.build(tmp / "runs", self.PRICING); latest = tmp / "latest.json"; latest.write_text(json.dumps(prev, indent=1))
        prev_row = json.loads(latest.read_text())["tables"]["search"][0]; before = json.loads((tmp / "quota.json").read_text())["used"]["exa"]
        s, ncalls = self._guard_run(tmp, "2026-10-19", fail_at=6)   # 5 billed data calls, then a 401
        p = s["providers"][0]
        self.assertEqual((p["status"], p["calls"], p["data_calls"], ncalls), ("stale_auth_error", 6, 5, 6))
        used = json.loads((tmp / "quota.json").read_text())["used"]["exa"]
        self.assertEqual(used - before, 5)   # all 5 pre-401 calls billed; the 401 = 0 units (main's assumption)
        doc = tables.build(tmp / "runs", self.PRICING); doc["run_summary"] = s
        self.assertEqual(doc["tables"]["search"][0]["run_date"], "2026-10-12")   # stale log ignored by collect
        doc["tables"]["search"][0]["generated_marker"] = "rebuilt"   # prove the kept row is the file's row, not a rebuild
        B.keep_previous_rows(doc, latest, {"exa"})
        new = doc["tables"]["search"]; self.assertEqual(len(new), 1)
        self.assertEqual(json.dumps(new[0], indent=1), json.dumps(prev_row, indent=1))
        self.assertIn(json.dumps(prev_row, indent=1).replace("\n", "\n   "), json.dumps(doc, indent=1))   # same bytes in latest.json
        self.assertEqual(json.loads(json.dumps(doc))["run_summary"]["providers"][0]["status"], "stale_auth_error")
        self.assertEqual(tables.untraced_cells(doc, tmp), [])

    def test_late_403_charged_estimate_and_kept_stale(self):
        tmp = Path(tempfile.mkdtemp(prefix="awi-test-"))
        s, _ = self._guard_run(tmp, "2026-10-19", fail_at=4, code=403)
        self.assertEqual(s["providers"][0]["status"], "stale_auth_error")
        self.assertEqual(json.loads((tmp / "quota.json").read_text())["used"]["exa"], 4)   # 3 pre-403 + the 403 at the estimate
        self.assertEqual(tables.build(tmp / "runs", self.PRICING)["tables"]["search"], [])   # no previous row: none published

    # ---------- CEO ruling 2026-10-19 (complete-only): incomplete -> stale_<reason>, previous row kept, spend logged ----------
    def _cmp_run(self, tmp, day, cfg=None, quota=1000, clock=None, fail_at=None):
        n = []
        def exa(key, item):
            n.append(1)
            if fail_at is not None and len(n) >= fail_at: raise RuntimeError("HTTP 500: upstream")
            return {"billed_units": 1, "results": [{"url": "https://a.example/x" if len(n) % 2 else "https://b.example/y", "title": "x"}]}
        pricing = {"exa": {"search": {"model": "credits", "free_tier": {"units_per_month": quota, "needs_card": False}}}}
        kw = {"clock": clock} if clock else {}
        s = runner.run(providers=self.PROV, sets={"search_evergreen": {"rows": self.ROWS, "sha": "x"}}, pricing=pricing, tos={},
                       adapters={"exa": {"search": exa}}, cfg=cfg or {}, runs_dir=tmp / "runs", ledger_path=tmp / "quota.json",
                       run_date=day, env={"EXA_API_KEY": "d"}, mock=True, **kw)
        return s["providers"][0], len(n), json.loads((tmp / "quota.json").read_text())["used"].get("exa", 0)

    def _publish(self, tmp, s_prov, prev_doc):
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        latest = tmp / "latest.json"; latest.write_text(json.dumps(prev_doc, indent=1))
        doc = tables.build(tmp / "runs", self.PRICING); skips = []; s = {"providers": [s_prov]}
        B.finish_doc(doc, s, CFG, latest, skips); return doc, skips, s

    def _prev(self, tmp):
        self._cmp_run(tmp, "2026-10-12"); d = tables.build(tmp / "runs", self.PRICING)
        return json.loads(json.dumps(d))

    def test_quota_stop_is_stale_quota_previous_row_kept(self):
        tmp = Path(tempfile.mkdtemp(prefix="awi-test-")); prev = self._prev(tmp)   # 8 units used by the earlier full run
        p, n, used = self._cmp_run(tmp, "2026-10-19", quota=11)                       # 3 more calls fit, then the guard stops it
        self.assertEqual((p["status"], p["calls"], n, used), ("stale_quota", 3, 3, 11))   # spend logged: 8 + 3
        doc, skips, s = self._publish(tmp, p, prev)
        self.assertEqual(json.dumps(doc["tables"]["search"], indent=1), json.dumps(prev["tables"]["search"], indent=1))
        self.assertEqual(doc["tables"]["search"][0]["run_date"], "2026-10-12"); self.assertEqual(skips, [])
        self.assertTrue(s["providers"][0]["reason"].endswith("not published; previous row kept"))
        lines = read_jsonl(tmp / "runs" / "2026-10-19" / "exa" / "search_evergreen.jsonl")   # stale run log still written
        self.assertEqual((len(lines), {l["run_status"] for l in lines}), (3, {"stale_quota"}))

    def test_timeout_is_stale_timeout(self):
        tmp = Path(tempfile.mkdtemp(prefix="awi-test-")); prev = self._prev(tmp); t = iter(range(0, 10000, 10))
        p, n, used = self._cmp_run(tmp, "2026-10-19", cfg={"run_budget_s": {"exa": 45}}, clock=lambda: next(t))
        self.assertEqual(p["status"], "stale_timeout"); self.assertLess(n, 8); self.assertEqual(used, 8 + n)   # spend logged
        doc, skips, _ = self._publish(tmp, p, prev)
        self.assertEqual(doc["tables"]["search"], prev["tables"]["search"]); self.assertEqual(skips, [])

    def test_all_errors_is_stale_error_spend_kept(self):
        tmp = Path(tempfile.mkdtemp(prefix="awi-test-")); prev = self._prev(tmp)
        p, n, used = self._cmp_run(tmp, "2026-10-19", fail_at=1)
        self.assertEqual((p["status"], n, used), ("stale_error", 8, 16))   # 8 errored calls billed at the estimate
        doc, _, _ = self._publish(tmp, p, prev)
        self.assertEqual(doc["tables"]["search"], prev["tables"]["search"])   # complete-but-all-errors never shown as 0%

    def test_first_ever_partial_run_is_omitted(self):
        tmp = Path(tempfile.mkdtemp(prefix="awi-test-"))
        p, n, used = self._cmp_run(tmp, "2026-10-19", quota=3)
        self.assertEqual((p["status"], used), ("stale_quota", 3))
        self.assertIn("exa", CFG["month1_free_tier_providers"])
        doc, skips, s = self._publish(tmp, p, {"tables": {"search": [], "scrape": [], "browser": []}, "freshness": []})
        self.assertEqual((doc["tables"]["search"], doc["freshness"], doc["not_run_yet"], skips), ([], [], [], []))
        self.assertFalse(doc["has_results"])
        self.assertTrue(s["providers"][0]["reason"].endswith("no previous row, not shown")); self.assertNotIn("previous row kept", s["providers"][0]["reason"])

    def test_full_set_run_still_publishes(self):
        tmp = Path(tempfile.mkdtemp(prefix="awi-test-")); prev = self._prev(tmp)
        p, n, used = self._cmp_run(tmp, "2026-10-19")
        self.assertEqual((p["status"], n, used), ("ran", 8, 16))
        doc, skips, _ = self._publish(tmp, p, prev)
        r = doc["tables"]["search"][0]
        self.assertEqual((r["run_date"], r["attempted"]["v"], r["partial"]), ("2026-10-19", 8, False))
        self.assertNotIn("run_status", read_jsonl(tmp / "runs" / "2026-10-19" / "exa" / "search_evergreen.jsonl")[0])

    # ---------- CEO billing ruling 2026-10-19: no response 0 (unbilled_no_response), 401 0, 403/429/5xx estimate ----------
    def _bill(self, exc_at, exc, n_rows=8, ledger=None):
        tmp = Path(tempfile.mkdtemp(prefix="awi-test-")); led = tmp / "quota.json"
        if ledger is not None: led.write_text(ledger)
        n = []
        def exa(key, item):
            n.append(1)
            if len(n) in exc_at: raise exc() if callable(exc) else exc
            return {"billed_units": 1, "results": [{"url": "https://a.example/x", "title": "x"}]}
        s = runner.run(providers=self.PROV, sets={"search_evergreen": {"rows": self.ROWS[:n_rows], "sha": "x"}}, pricing=self.PRICING, tos={},
                       adapters={"exa": {"search": exa}}, cfg={}, runs_dir=tmp / "runs", ledger_path=led, run_date="2026-10-19", env={"EXA_API_KEY": "d"}, mock=True)
        f = tmp / "runs" / "2026-10-19" / "exa" / "search_evergreen.jsonl"
        return s["providers"][0], (read_jsonl(f) if f.exists() else []), led

    NO_RESPONSE = [lambda: __import__("urllib.error").error.URLError(__import__("socket").gaierror(-2, "Name or service not known")),
                   lambda: ConnectionRefusedError(111, "Connection refused"), lambda: ConnectionResetError(104, "Connection reset by peer"),
                   lambda: TimeoutError("timed out"), lambda: __import__("urllib.error").error.URLError("[Errno 101] Network is unreachable"),
                   lambda: RuntimeError("URLError: <urlopen error [Errno -3] Temporary failure in name resolution>")]   # adapter re-raise, key masked

    def test_no_response_every_call_ledger_byte_identical(self):
        before = json.dumps({"month": "2026-10", "over_free_units": 0, "used": {"exa": 33}}, indent=1, sort_keys=True)
        for mk in self.NO_RESPONSE:
            p, lines, led = self._bill(set(range(1, 9)), mk, ledger=before)
            self.assertEqual(p["status"], "stale_error", mk()); self.assertEqual(led.read_text(), before)   # byte-identical
            self.assertEqual(lines, [])   # nothing billed, nothing returned: no run lines

    def test_no_response_line_is_unbilled_no_response(self):   # 1 of 8 gets no response -> 0 units, set incomplete -> stale_error
        for mk in self.NO_RESPONSE:
            p, lines, led = self._bill({4}, mk)
            self.assertEqual((p["status"], p["calls"], p["data_calls"]), ("stale_error", 8, 7), mk())
            self.assertIn("incomplete set: search_evergreen 7/8", p["reason"])
            self.assertEqual([(l["status"], l["billed_units"]) for l in lines if l["status"] not in ("pass", "fail")], [("unbilled_no_response", 0)])
            self.assertEqual(json.loads(led.read_text())["used"]["exa"], 7)

    def test_401_bills_zero_403_429_5xx_bill_estimate(self):
        for code, units, st in ((401, 0, "stale_auth_error"), (403, 1, "stale_auth_error")):
            p, lines, led = self._bill({8}, RuntimeError(f"HTTP {code}: denied"))
            self.assertEqual((p["status"], lines[-1]["billed_units"], json.loads(led.read_text())["used"]["exa"]), (st, units, 7 + units), code)
        import io, urllib.error
        for code in (429, 500, 502, 503):
            for exc in (RuntimeError(f"HTTP {code}: upstream"), urllib.error.HTTPError("https://x.example", code, "x", {}, io.BytesIO(b""))):
                self.assertFalse(runner.is_network_error(exc), code)
                p, lines, led = self._bill({8}, exc)
                self.assertEqual((p["status"], lines[-1]["status"], lines[-1]["billed_units"], json.loads(led.read_text())["used"]["exa"]), ("ran", "fail", 1, 8), code)
        self.assertTrue(runner.is_network_error(TimeoutError("timed out"))); self.assertFalse(runner.is_network_error(RuntimeError("HTTP 500: Timed out.")))

    def test_runner_short_set_is_stale_error(self):   # kills NEW1 (short-set check) and NEW4 (off-by-one): runner, not tables
        for at in ({8}, {1}, {3, 5}):
            p, lines, _ = self._bill(at, ConnectionRefusedError(111, "Connection refused"))
            k = 8 - len(at)
            self.assertEqual(p["status"], "stale_error", at); self.assertEqual(p["data_calls"], k)
            self.assertIn(f"incomplete set: search_evergreen {k}/8", p["reason"])
        p, _, _ = self._bill(set(), None)
        self.assertEqual(p["status"], "ran")   # full set: not stale

    def test_stale_reasons(self):
        self.assertEqual(runner.STALE_REASONS, ("quota", "timeout", "auth_error", "error"))
        with self.assertRaises(AssertionError): runner.stale("partial", {"calls": 0, "data_calls": 0}, "x")

    def test_partial_counts_only_scored_or_tos_lines(self):
        mk = lambda i, st: (i, {"item_id": f"q{i}", "status": st, "set_rows": 3})
        self.assertFalse(tables.is_partial([mk(0, "pass"), mk(1, "fail"), mk(2, "skipped_tos")]))
        self.assertTrue(tables.is_partial([mk(0, "pass"), mk(1, "fail"), mk(2, "auth_error")]))
        self.assertTrue(tables.is_partial([mk(0, "pass"), mk(1, "fail"), mk(2, "weird")]))
        tmp = Path(tempfile.mkdtemp(prefix="awi-test-"))
        self._guard_run(tmp, "2026-10-19", n_rows=8)
        f = tmp / "runs" / "2026-10-19" / "exa" / "search_evergreen.jsonl"; ls = read_jsonl(f)
        f.write_text("".join(json.dumps(l) + "\n" for l in ls[:5]))   # CEO 2026-10-19: an incomplete log is never published
        self.assertEqual(tables.build(tmp / "runs", self.PRICING)["tables"]["search"], [])

    def test_complete_set_is_not_partial(self):
        tmp = Path(tempfile.mkdtemp(prefix="awi-test-"))
        s, n = self._guard_run(tmp, "2026-10-19")
        self.assertEqual((s["providers"][0]["status"], n), ("ran", 8))
        r = tables.build(tmp / "runs", self.PRICING)["tables"]["search"][0]
        self.assertEqual((r["partial"], r["attempted"]["v"], r["passed"]["v"]), (False, 8, 4))
        self.assertNotIn("auth_error", r)

    def test_no_resource_warning(self):   # runner/tables close every file (with open); run the guard tests with ResourceWarning as error
        tests = [f"tests.test_bench.Rulings3.{t}" for t in ("test_late_401_previous_row_byte_identical", "test_late_401_keeps_data_rows_and_spend", "test_complete_set_is_not_partial")]
        r = subprocess.run([sys.executable, "-X", "dev", "-W", "error::ResourceWarning", "-m", "unittest", *tests], cwd=BENCH, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:]); self.assertNotIn("ResourceWarning", r.stderr)

    # ---------- PR #2 follow-up: stale providers never surface; stale-only runs leave latest.json alone ----------
    def _row(self, pid, v): return {"provider": pid, "set": "news_2026-42", "run_date": v, "attempted": {"v": 1, "src": []}}
    def _fr(self, pid, v): return {"provider": pid, "set": "news_2026-42", "run_date": v, "fresh_hit_at_10": {"v": 0.5, "src": []}}

    def test_stale_provider_not_in_skips_or_not_run_yet(self):   # kills R1, and the not_run_yet bug (evidence case4)
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        tmp = Path(tempfile.mkdtemp(prefix="awi-test-")); prev = tmp / "latest.json"
        prev.write_text(json.dumps({"tables": {"search": [self._row("exa", "2026-10-12")], "scrape": [], "browser": []}, "freshness": []}, indent=1))
        s = {"providers": [{"provider": "exa", "category": "search", "status": "stale_auth_error", "data_calls": 5, "reason": "HTTP 401"},
                           {"provider": "tavily", "category": "search", "status": "stale_auth_error", "data_calls": 0, "reason": "HTTP 401"}]}
        doc = {"tables": {"search": [], "scrape": [], "browser": []}, "freshness": []}; skips = []
        self.assertEqual(B.finish_doc(doc, s, CFG, prev, skips), {"exa", "tavily"})
        self.assertIn("tavily", CFG["month1_free_tier_providers"])
        self.assertEqual(skips, []); self.assertEqual(doc["not_run_yet"], [])
        self.assertEqual([r["provider"] for r in doc["tables"]["search"]], ["exa"])

    def test_stale_provider_freshness_kept(self):   # kills R2
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        tmp = Path(tempfile.mkdtemp(prefix="awi-test-")); prev = tmp / "latest.json"; old = self._fr("exa", "2026-10-12")
        prev.write_text(json.dumps({"tables": {"search": [self._row("exa", "2026-10-12")]}, "freshness": [old, self._fr("tavily", "2026-10-12")]}, indent=1))
        doc = {"tables": {"search": [self._row("exa", "2026-10-19"), self._row("tavily", "2026-10-19")]},
               "freshness": [self._fr("exa", "2026-10-19"), self._fr("tavily", "2026-10-19")]}
        B.keep_previous_rows(doc, prev, {"exa"})
        self.assertEqual(sorted((r["provider"], r["run_date"]) for r in doc["freshness"]), [("exa", "2026-10-12"), ("tavily", "2026-10-19")])
        self.assertEqual(sorted((r["provider"], r["run_date"]) for r in doc["tables"]["search"]), [("exa", "2026-10-12"), ("tavily", "2026-10-19")])

    def test_stale_without_previous_row_not_published_from_older_log(self):   # kills R3
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        tmp = Path(tempfile.mkdtemp(prefix="awi-test-"))
        self._guard_run(tmp, "2026-10-12")                     # older good log on disk
        s, _ = self._guard_run(tmp, "2026-10-19", fail_at=3)   # then a stale run
        prev = tmp / "latest.json"; prev.write_text(json.dumps({"tables": {"search": [], "scrape": [], "browser": []}, "freshness": []}))
        doc = tables.build(tmp / "runs", self.PRICING)
        self.assertEqual([r["run_date"] for r in doc["tables"]["search"]], ["2026-10-12"])   # collect falls back to the older log
        B.finish_doc(doc, s, CFG, prev, [])
        self.assertEqual(doc["tables"]["search"], []); self.assertEqual(doc["freshness"], []); self.assertEqual(doc["not_run_yet"], [])

    def test_stale_only_run_leaves_latest_json(self):   # main(): data only from a stale provider -> latest.json byte-identical
        import shutil, io, urllib.error
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        root = Path(tempfile.mkdtemp(prefix="awi-test-")) / "site"
        shutil.copytree(SITE / "bench", root / "bench", ignore=shutil.ignore_patterns("__pycache__")); shutil.copytree(SITE / "data", root / "data")
        (root / "collectors").mkdir(); shutil.copy(SITE / "collectors" / "providers.json", root / "collectors")
        out = root / "data" / "benchmarks" / "latest.json"; before = out.read_bytes()
        led = root / "data" / "spend" / f"{dt.date.today().isoformat()[:7]}-quota.json"
        used0 = json.loads(led.read_text())["used"].get("exa", 0) if led.exists() else 0
        calls = []
        class Resp:
            status = 200
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s): return json.dumps({"results": [{"url": "https://a.example/x", "title": "t"}]}).encode()
        def fake(req, timeout=None):
            calls.append(1)
            if len(calls) == 3: raise urllib.error.HTTPError(req.full_url, 401, "x", {}, io.BytesIO(b"bad key"))
            return Resp()
        env = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_API_TOKEN", "_TOKEN", "_KEY"))} | {"EXA_API_KEY": "mock-dummy"}
        with mock.patch.object(B, "ROOT", root), mock.patch.object(B, "BENCH", root / "bench"), mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(sys, "argv", ["benchmark.py", "--mode", "full", "--only-providers", "exa"]), mock.patch("urllib.request.urlopen", fake), \
             mock.patch("builtins.print") as pr:
            self.assertEqual(B.main(), 0)
        self.assertEqual(len(calls), 3)
        self.assertEqual(out.read_bytes(), before)   # generated_at and run_summary unchanged
        self.assertTrue(any("SKIP publish" in " ".join(map(str, c.args)) for c in pr.call_args_list))
        self.assertEqual(json.loads(led.read_text())["used"]["exa"] - used0, 2 * 1)   # both pre-401 billed calls kept; 401 = 0
        self.assertFalse(B.fresh_data({"providers": [{"provider": "exa", "status": "stale_auth_error", "data_calls": 2}]}))
        self.assertTrue(B.fresh_data({"providers": [{"provider": "exa", "status": "stale_auth_error", "data_calls": 2}, {"provider": "tavily", "status": "ran", "data_calls": 1}]}))

    def test_keyed_providers(self):
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        provs = [{"id": "exa", "key_env": "EXA_API_KEY"}, {"id": "steel", "key_env": "STEEL_API_KEY"}, {"id": "tavily", "key_env": "TAVILY_API_KEY"}]
        self.assertEqual(B.keyed_providers(provs, {}), [])
        self.assertEqual(B.keyed_providers(provs, {"EXA_API_KEY": "", "STEEL_API_KEY": "x"}), [])   # empty key, and steel has no collector
        self.assertEqual(B.keyed_providers(provs, {"TAVILY_API_KEY": "x"}), ["tavily"])

    def test_workflow_guards_published_data(self):
        w = (SITE / ".github" / "workflows" / "benchmark.yml").read_text()
        self.assertIn("SKIP publish", w); self.assertIn("git checkout -- data/benchmarks", w)

    def test_feed_format_and_robots_guards(self):
        from awibench import newsgen
        self.assertTrue(newsgen.is_feed_xml('<?xml version="1.0"?>\n<rss version="2.0"><channel>'))
        self.assertTrue(newsgen.is_feed_xml('<?xml version="1.0"?><!-- c --><feed xmlns="http://www.w3.org/2005/Atom">'))
        self.assertFalse(newsgen.is_feed_xml("<!DOCTYPE html><html><body>Access denied</body></html>"))
        hn = "User-Agent: *\nCrawl-delay: 30\nDisallow: /login\nDisallow: /r?\nDisallow: /x?\n"
        self.assertTrue(newsgen.robots_allows("https://news.ycombinator.com/rss", hn))     # stdlib robotparser gets this wrong
        self.assertFalse(newsgen.robots_allows("https://news.ycombinator.com/r?id=1", hn))
        self.assertFalse(newsgen.robots_allows("https://a.example/feed", "User-agent: AgentWebIndexBot\nDisallow: /\n\nUser-agent: *\nAllow: /"))
        self.assertTrue(newsgen.robots_allows("https://a.example/a/b.xml", "User-agent: *\nDisallow: /*.xml$\nAllow: /a/b.xml$"))
        self.assertTrue(newsgen.robots_allows("https://a.example/feed", "User-agent: *\nAllow: /feed\nDisallow: /feed"))   # equal length: Allow wins ties (M8)
        self.assertTrue(newsgen.robots_allows("https://a.example/feed", "User-agent: *\nDisallow: /feed\nAllow: /feed"))
        log = []; cfg = {"news": CFG["news"], "news_feeds": [{"id": "f", "url": "https://a.example/f"}]}
        newsgen.generate(cfg, __import__("datetime").datetime(2026, 10, 26, 6), "0", lambda f: "<html>nope</html>", log)
        self.assertEqual(log[0]["event"], "feed_skipped")

    def test_feeds_recorded_as_checked(self):
        live = [f for f in CFG["news_feeds"] if f.get("url")]
        self.assertTrue(all(f.get("feed_format") in ("rss", "atom") and f.get("robots_checked") for f in live))

    def test_browserless_kept_with_tos_date(self):
        t = load_yaml(BENCH / "tos_matrix.yaml")["providers"]["browserless"]
        self.assertEqual(t["checked_at"], "2026-10-19"); self.assertIn("2026-10-19", t["tos_checks"])
        self.assertFalse(runner.benchmark_forbidden(t))
        doc = json.loads((SITE / "data" / "benchmarks" / "latest.json").read_text())
        row = [r for r in doc["tables"]["browser"] if r["provider"] == "browserless"][0]
        self.assertEqual((row["passed"]["v"], row["attempted"]["v"]), (11, 16))

if __name__ == "__main__": unittest.main()
