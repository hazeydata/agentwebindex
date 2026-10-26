"""SPEC-bench AC tests. MOCK data only; all outputs go to a temp dir. Run: python -m unittest -v (cwd bench/)."""
import contextlib, datetime as dt, hashlib, json, math, os, re, shutil, subprocess, sys, unittest
from pathlib import Path
from unittest import mock
BENCH = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(BENCH)); sys.path.insert(0, str(Path(__file__).parent))
from awibench import validate, newsgen, cost, freshness, runner, tables, secrets_scan, freeze, checks, guard as guard_mod, gates as gates_mod, confirmation_diff
from awibench.util import load_yaml, read_jsonl, sha256_file, SITE
import mocks

def mktmp(tc):
    n = getattr(tc, "_tmp_n", 0) + 1; tc._tmp_n = n
    p = tc.tmp_path / f"d{n}"; p.mkdir(); return p

def tmpd(tc):
    return contextlib.nullcontext(str(mktmp(tc)))

CFG = load_yaml(BENCH / "config.yaml"); RSS = Path(__file__).parent / "fixtures" / "rss"
RUN_AT = dt.datetime.fromisoformat("2026-10-19T06:00:00-04:00")


def parse_workflow_yaml(text):
    """Minimal block-YAML reader for the workflow files (PyYAML is optional here): mappings, '- ' lists, one-line flow maps
    `{k: v, ...}`, `|` block scalars, quoted scalars and comments. Enough to read on/jobs/steps/env/run; unknown shapes raise."""
    def scalar(v):
        v = v.strip()
        if v.startswith('"'): return v[1:v.index('"', 1)]
        if v.startswith("{"):
            body = v[1:v.rindex("}")].strip(); out = {}
            parts, depth, cur = [], 0, ""
            for ch in body:
                if ch in "{[": depth += 1
                if ch in "}]": depth -= 1
                if ch == "," and depth == 0: parts.append(cur); cur = ""
                else: cur += ch
            if cur.strip(): parts.append(cur)
            for part in parts:
                k, _, val = part.partition(":"); out[k.strip()] = scalar(val)
            return out
        return v.split(" #")[0].strip()
    lines = [l for l in text.splitlines()]
    def strip_comment(l): return "" if l.lstrip().startswith("#") else l
    def block(i, indent):   # returns (value, next_i)
        items = []; mapping = {}; is_list = None
        while i < len(lines):
            raw = lines[i]; l = strip_comment(raw)
            if not l.strip(): i += 1; continue
            ind = len(l) - len(l.lstrip())
            if ind < indent: break
            if ind > indent: raise ValueError(f"unexpected indent at line {i+1}: {raw!r}")
            body = l.strip()
            if body.startswith("- "):
                is_list = True; sub = " " * (ind + 2) + body[2:]; lines[i] = sub
                val, i = block(i, ind + 2); items.append(val); continue
            k, sep, v = body.partition(":")
            if not sep: raise ValueError(f"not a mapping at line {i+1}: {raw!r}")
            v = v.strip(); i += 1
            if v.split(" #")[0].strip() == "|":
                buf = []
                while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > ind):
                    buf.append(lines[i]); i += 1
                cut = min((len(b) - len(b.lstrip()) for b in buf if b.strip()), default=0)
                mapping[k.strip()] = "\n".join(b[cut:] for b in buf).rstrip("\n") + "\n"
            elif v == "" or v.startswith("#"):
                nxt = i
                while nxt < len(lines) and not strip_comment(lines[nxt]).strip(): nxt += 1
                if nxt < len(lines) and len(lines[nxt]) - len(lines[nxt].lstrip()) > ind:
                    sub_ind = len(lines[nxt]) - len(lines[nxt].lstrip()); mapping[k.strip()], i = block(nxt, sub_ind)
                else: mapping[k.strip()] = None
            else: mapping[k.strip()] = scalar(v)
        return (items if is_list else mapping), i
    doc, _ = block(0, 0); return doc

class T(unittest.TestCase):
    def setUp(self): self.tmp = mktmp(self); self.cfg = mocks.mock_cfg(CFG)

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
    def test_ac1_real_sets_dir_validates(self):
        errs, _ = validate.validate(BENCH / "sets", CFG); self.assertEqual(errs, [])
    def test_ac1_real_sets_match_freeze_manifest(self):
        self.assertEqual(CFG["freeze_manifest"], "freeze-2026-10-20-thirdpass.json")
        sums = json.loads((BENCH / "sets" / CFG["freeze_manifest"]).read_text())["sha256"]
        self.assertEqual(sorted(sums), ["browser_tasks.yaml", "scrape_urls.jsonl", "search_evergreen.jsonl"])
        for f, h in sums.items(): self.assertEqual(sha256_file(BENCH / "sets" / f), h, f)
        self.assertEqual(validate.check_freeze(BENCH / "sets", CFG["freeze_manifest"]), [])
        d = mktmp(self); (d / "scrape_urls.jsonl").write_text("x")
        self.assertEqual(validate.check_freeze(d, CFG["freeze_manifest"]), [f"missing freeze manifest {CFG['freeze_manifest']}"])
    def test_search_check_tolerates_null_title_and_snippet(self):
        self.assertEqual(checks.search_check({"type": "snippet_contains", "value": "x"}, [{"url": "https://a.example/", "title": None, "snippet": None}]), (False, "snippet not found in top5"))
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
            n = u.split("scrape")[1].split(".")[0]; body = "x" * 5000 + f" marker phrase {n}"
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
        self.tmp = mktmp(self); sd = self.tmp / "sets"; ev, sc, ts = mocks.make_sets(sd)
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
    def test_run_log_records_adapter_config_name(self):
        kw, _, _ = self._setup_run(); pid = kw["providers"][0]["id"]; cat = kw["providers"][0]["categories"][0]
        kw["adapters"] = {pid: {cat: lambda key, item: {"results": [], "config": "named-x"}}}
        runner.run(**kw)
        lines = [json.loads(l) for f in Path(kw["runs_dir"]).rglob("*.jsonl") for l in f.read_text().splitlines()]
        self.assertTrue(lines); self.assertEqual({l.get("config") for l in lines if l["status"] in ("pass", "fail")}, {"named-x"})
    def test_missing_set_reason_has_no_path(self):
        with tmpd(self) as td:
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
                          ledger_path=self.tmp / "rspend.json", spend_dir=(self.tmp / "bdspend").mkdir(exist_ok=True) or self.tmp / "bdspend", run_date="2026-10-19", env={p["key_env"]: "mock-not-a-key" for p in provs}, mock=True, meters={"apify": lambda: 0.0},
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
        self.assertEqual(st[("contextdev", "search")], "skipped_no_vendor_confirmation")   # AC6a: committed config keeps contextdev out of live runs
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
        self.assertEqual(n, 23)   # 19 + apify search (2026-10-19) + browserless scrape (AWI-EVERGREEN-RUN-001) + contextdev search/scrape
        self.assertIsNone(cost.cost_columns(pricing["browserbase"]["browser"], [{"status": "pass", "billed_units": 1.0, "proxy_gb": 0}])["entry_tier_cost_per_1k"])

    # ---------- CEO final changes ----------
    def test_7_providers_json_has_no_quota_or_cost_fields(self):
        provs = json.loads((SITE / "collectors/providers.json").read_text())["providers"]
        banned = {"free_quota", "entry_cost", "costs", "cost", "pricing", "free_tier", "quota", "units_per_month", "unit_price", "tiers"}
        for p in provs:
            self.assertEqual(set(p) & banned, set(), p["id"]); self.assertEqual(set(p) - {"exclude_from_also_priced"}, {"id", "name", "categories", "pricing_url", "key_env"}, p["id"])   # exclude_from_also_priced: optional R3 flag
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
            with tmpd(self) as d:
                recs = [{"status": "pass"}] * 5 + [{"status": "fail", "reason": "no match"}] * 5 + \
                       [{"status": "fail", "reason": 'RuntimeError: HTTP 429: {"error":"Rate limit"}'}] + [{"status": "fail", "http_status": 429, "reason": "x"}]
                doc = tables.build(self._log(d, pid, recs), {}, None, {})
                r = doc["tables"]["search"][0]
                self.assertEqual((r["attempted"]["v"], r["passed"]["v"], r["failed"]["v"], r["rate_limited"]["v"]), (10, 5, 5, 2))
                self.assertEqual(r["success_rate"]["v"], 0.5)
                self.assertEqual(r["answered_display"], "5/10 answered + 2 rate-limited (free tier)")
                self.assertIn("free-tier rate limit is the cause", r["rate_limited"]["note"])
                self.assertIn("below minimum (n=12)", r["flags"])
                self.assertEqual(tables.untraced_cells(doc, Path(d)), [])

    RL = {"status": "fail", "reason": 'RuntimeError: HTTP 429: {"error":"Rate limit"}'}

    def test_rule3_6_of_16_429_is_quota_degraded(self):
        with tmpd(self) as d:
            recs = [{"status": "pass"}] * 5 + [{"status": "fail", "reason": "no match"}] * 5 + [self.RL] * 5 + [{"status": "fail", "http_status": 429, "reason": "x"}]
            self.assertTrue(runner.quota_degraded(recs))
            self.assertEqual(tables.build(self._log(d, "firecrawl", recs), {}, None, {})["tables"]["search"], [])

    def test_rule3_skipped_tos_lines_are_not_calls(self):
        recs = [{"status": "pass"}] * 12 + [self.RL] * 4 + [{"status": "skipped_tos"}] * 4
        self.assertTrue(runner.quota_degraded(recs))
        with tmpd(self) as d:
            self.assertEqual(tables.build(self._log(d, "firecrawl", recs), {}, None, {})["tables"]["search"], [])

    def test_rule3_collect_counts_only_offset0_call_lines(self):
        with tmpd(self) as d:
            recs = [{"status": "pass"}] * 13 + [self.RL] * 3
            runs = self._log(d, "firecrawl", recs)
            f = runs / "2026-10-15" / "firecrawl" / "news_2099-01.jsonl"
            extra = [{"ts": "2026-10-16T00:00:00+00:00", "set": "news_2099-01", "set_sha": "x", "set_rows": 16, "offset_h": 24, "mock": True,
                      "item_id": f"i{n}", "billed_units": 1} | self.RL for n in range(13)]
            f.write_text(f.read_text() + "".join(json.dumps(r) + "\n" for r in extra))
            self.assertIn(("firecrawl", "news_2099-01"), tables.collect(runs))
            f.write_text(f.read_text() + json.dumps(extra[0] | {"offset_h": 0}) + "\n")
            self.assertNotIn(("firecrawl", "news_2099-01"), tables.collect(runs))

    def test_rule3_rebuild_dropped_provider_absent_from_table_and_not_run_yet(self):
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        with tmpd(self) as d:
            runs = self._log(d, "firecrawl", [{"status": "pass"}] * 5 + [{"status": "fail", "reason": "no match"}] * 5 + [self.RL] * 6)
            f = Path(d) / "runs" / "2026-10-15" / "exa" / "news_2099-01.jsonl"; f.parent.mkdir(parents=True)
            f.write_text("".join(json.dumps({"ts": "2026-10-15T00:00:00+00:00", "set": "news_2099-01", "set_sha": "x", "set_rows": 16, "offset_h": 0,
                                             "mock": True, "latency_ms": 1, "billed_units": 1, "item_id": f"i{n}", "status": "pass"}) + "\n" for n in range(16)))
            doc = tables.build(runs, {}, None, {}); skips = []
            s = {"providers": [{"provider": "exa", "category": "search", "status": "ran", "calls": 16, "data_calls": 16},
                               {"provider": "firecrawl", "category": "search", "status": "ran", "calls": 16, "data_calls": 10},
                               {"provider": "brightdata", "category": "search", "status": "skipped_no_key", "reason": "not run (no key)"}]}
            latest = Path(d) / "latest.json"; latest.write_text(json.dumps({"tables": {"search": [], "scrape": [], "browser": []}, "freshness": []}))
            B.finish_doc(doc, s, CFG, latest, skips)
            self.assertEqual([r["provider"] for r in doc["tables"]["search"]], ["exa"])
            self.assertEqual([n["provider"] for n in doc["not_run_yet"]], ["brightdata"])
            self.assertNotIn("firecrawl", {k.get("provider") for k in skips})

    def test_rule3_rebuild_drops_unstamped_old_log_over_20pct_429(self):
        rl = {"status": "fail", "reason": 'RuntimeError: HTTP 429: {"error":"Rate limit"}'}
        for n429, n_ok, shown in ((6, 10, False), (4, 12, False), (3, 12, True), (2, 14, True), (0, 16, True)):
            with tmpd(self) as d:
                recs = [{"status": "pass"}] * n_ok + [rl] * n429
                rows = tables.build(self._log(d, "firecrawl", recs), {}, None, {})["tables"]["search"]
                self.assertEqual(bool(rows), shown, (n429, n_ok))
        keys = tables.collect(SITE / "bench" / "runs")
        self.assertNotIn(("firecrawl", "news_2026-42"), keys); self.assertIn(("exa", "news_2026-42"), keys)

    def test_no_429_has_no_rate_limited_value(self):
        with tmpd(self) as d:
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
    def _row(self, cat, pid, set_name=None):
        return next(r for r in self.DOC["tables"][cat] if r["provider"] == pid and set_name in (None, r["set"]))

    def test_firecrawl_2026_10_15_search_row_dropped_by_rule3(self):
        self.assertNotIn("firecrawl", {r["provider"] for r in self.DOC["tables"]["search"]})
        self.assertNotIn("firecrawl", {n["provider"] for n in self.DOC["not_run_yet"]})
        self.assertIn(("firecrawl", "scrape", "skipped_no_key"), {(k.get("provider"), k.get("category"), k["status"]) for k in self.DOC["skips"]})
        recs = read_jsonl(SITE / "bench" / "runs" / "2026-10-15" / "firecrawl" / "news_2026-42.jsonl")
        self.assertEqual(sum(tables.is_rate_limited(x) for x in recs), 6); self.assertTrue(runner.quota_degraded(recs))
        for pid in ("exa", "tavily"): self.assertIsNone(self._row("search", pid)["rate_limited"]["v"])

    def test_run1_counts_match_raw_logs(self):
        want = {"exa": (12, 16, 0), "tavily": (8, 16, 0)}
        for pid, (pas, att, rl) in want.items():
            recs = read_jsonl(SITE / "bench" / "runs" / "2026-10-15" / pid / "news_2026-42.jsonl")
            n429 = sum(tables.is_rate_limited(x) for x in recs)
            scored = [x for x in recs if not tables.is_rate_limited(x)]
            self.assertEqual((sum(x["status"] == "pass" for x in scored), len(scored), n429), (pas, att, rl), pid)
            # #18: the published row is the raw log re-graded with the current grader (the log itself stays raw)
            lines, gv = tables.regrade("news_2026-42", [(i, x) for i, x in enumerate(recs, 1) if not tables.is_rate_limited(x)], SITE / "bench" / "sets")
            r = self._row("search", pid, "news_2026-42"); self.assertEqual((r["passed"]["v"], r["attempted"]["v"]), (sum(x["status"] == "pass" for _, x in lines), att))
            self.assertEqual(r["grader_version"], gv); self.assertEqual(r["run_date"], "2026-10-15")

    def test_below_minimum_flag_on_every_week42_row(self):
        rows = [r for t in self.DOC["tables"].values() for r in t] + self.DOC["freshness"]
        wk42 = [r for r in rows if r["set"].endswith("_2026-42")]
        for r in rows:
            if r not in wk42: self.assertNotIn("below minimum (n=16)", r["flags"], r["provider"])
        rows = wk42; self.assertTrue(rows)
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

    def test_apify_t60_config_pins_60s_and_120s_client_wait(self):
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B, urllib.parse as up
        self.assertEqual(CFG["apify_search_configs"]["rag-raw-http-t60"], {"scrapingTool": "raw-http", "requestTimeoutSecs": 60})
        seen = []; old_get, old_load = B._get_json, B.load_yaml
        B._get_json = lambda url, headers, timeout=150: (seen.append((url, timeout)), (200, []))[1]
        B.load_yaml = lambda p: dict(old_load(p), apify_search_config="rag-raw-http-t60")
        try: out = B._apify_search("k", {"query": "q"})
        finally: B._get_json, B.load_yaml = old_get, old_load
        qs = up.parse_qs(up.urlparse(seen[0][0]).query)
        self.assertEqual((qs["requestTimeoutSecs"], qs["scrapingTool"], seen[0][1], out["config"]), (["60"], ["raw-http"], 120, "rag-raw-http-t60"))
    def test_apify_named_config_sets_request_timeout_and_is_logged(self):
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B, urllib.parse as up
        self.assertEqual(CFG["apify_search_config"], "rag-raw-http-t120")
        self.assertEqual(CFG["apify_search_configs"]["rag-raw-http-t120"], {"scrapingTool": "raw-http", "requestTimeoutSecs": 120})
        seen = []; old = B._get_json; B._get_json = lambda url, headers, timeout=150: (seen.append((url, timeout)), (200, []))[1]
        try: out = B._apify_search("k", {"query": "q"})
        finally: B._get_json = old
        qs = up.parse_qs(up.urlparse(seen[0][0]).query)
        self.assertEqual((qs["requestTimeoutSecs"], qs["scrapingTool"], seen[0][1], out["config"]), (["120"], ["raw-http"], 180, "rag-raw-http-t120"))

    def test_late_run_gets_no_freshness_row(self):
        with tmpd(self) as d:
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
        tmp = mktmp(self)
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
        tmp = mktmp(self)
        self._guard_run(tmp, "2026-10-12")
        prev = tables.build(tmp / "runs", self.PRICING); latest = tmp / "latest.json"; latest.write_text(json.dumps(prev, indent=1))
        prev_row = json.loads(latest.read_text())["tables"]["search"][0]; before = json.loads((tmp / "quota.json").read_text())["used"]["exa"]
        s, ncalls = self._guard_run(tmp, "2026-10-19", fail_at=6)   # 5 billed data calls, then a 401
        p = s["providers"][0]
        self.assertEqual((p["status"], p["calls"], p["data_calls"], ncalls), ("stale_auth_error", 6, 5, 6))
        used = json.loads((tmp / "quota.json").read_text())["used"]["exa"]
        self.assertEqual(used - before, 5)
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
        tmp = mktmp(self)
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
        tmp = mktmp(self); prev = self._prev(tmp)   # 8 units used by the earlier full run
        p, n, used = self._cmp_run(tmp, "2026-10-19", quota=11)                       # 3 more calls fit, then the guard stops it
        self.assertEqual((p["status"], p["calls"], n, used), ("stale_quota", 3, 3, 11))   # spend logged: 8 + 3
        doc, skips, s = self._publish(tmp, p, prev)
        self.assertEqual(json.dumps(doc["tables"]["search"], indent=1), json.dumps(prev["tables"]["search"], indent=1))
        self.assertEqual(doc["tables"]["search"][0]["run_date"], "2026-10-12"); self.assertEqual(skips, [])
        self.assertTrue(s["providers"][0]["reason"].endswith("not published; previous row kept"))
        lines = read_jsonl(tmp / "runs" / "2026-10-19" / "exa" / "search_evergreen.jsonl")   # stale run log still written
        self.assertEqual((len(lines), {l["run_status"] for l in lines}), (3, {"stale_quota"}))

    def _r3_run(self, tmp, day, n429):
        rows = [{"id": f"r{i}", "query": f"r {i}", "check": {"type": "url_exact", "value": "https://a.example/x"}} for i in range(40)]
        n = []
        def exa(key, item):
            n.append(1)
            if len(n) <= n429: raise RuntimeError('HTTP 429: {"error":"Rate limit"}')
            return {"billed_units": 1, "results": [{"url": "https://a.example/x", "title": "x"}]}
        pricing = {"exa": {"search": {"model": "credits", "free_tier": {"units_per_month": 1000, "needs_card": False}}}}
        s = runner.run(providers=self.PROV, sets={"search_evergreen": {"rows": rows, "sha": "x"}}, pricing=pricing, tos={},
                       adapters={"exa": {"search": exa}}, cfg={}, runs_dir=tmp / "runs", ledger_path=tmp / "quota.json",
                       run_date=day, env={"EXA_API_KEY": "d"}, mock=True)
        return s["providers"][0], json.loads((tmp / "quota.json").read_text())["used"]["exa"]

    def test_rule3_429_share_over_20pct_is_stale_quota(self):
        for n429, st in ((37, "stale_quota"), (9, "stale_quota"), (8, "ran"), (0, "ran")):
            tmp = mktmp(self)
            self._r3_run(tmp, "2026-10-12", 0)
            prev = json.loads(json.dumps(tables.build(tmp / "runs", self.PRICING)))
            p, used = self._r3_run(tmp, "2026-10-19", n429)
            self.assertEqual((p["status"], p["calls"], used), (st, 40, 80), n429)
            doc, skips, s = self._publish(tmp, p, prev)
            row = doc["tables"]["search"][0]
            if st == "stale_quota":
                self.assertEqual(json.dumps(doc["tables"]["search"], indent=1), json.dumps(prev["tables"]["search"], indent=1), n429)
                self.assertIn(f"{n429}/40 calls answered HTTP 429", p["reason"])
                self.assertTrue(s["providers"][0]["reason"].endswith("not published; previous row kept"))
                lines = read_jsonl(tmp / "runs" / "2026-10-19" / "exa" / "search_evergreen.jsonl")
                self.assertEqual({l["run_status"] for l in lines}, {"stale_quota"})
            else:
                self.assertEqual((row["run_date"], row["attempted"]["v"]), ("2026-10-19", 40 - n429), n429)

    def test_timeout_is_stale_timeout(self):
        tmp = mktmp(self); prev = self._prev(tmp); t = iter(range(0, 10000, 10))
        p, n, used = self._cmp_run(tmp, "2026-10-19", cfg={"run_budget_s": {"exa": 45}}, clock=lambda: next(t))
        self.assertEqual(p["status"], "stale_timeout"); self.assertLess(n, 8); self.assertEqual(used, 8 + n)   # spend logged
        doc, skips, _ = self._publish(tmp, p, prev)
        self.assertEqual(doc["tables"]["search"], prev["tables"]["search"]); self.assertEqual(skips, [])

    def test_all_errors_is_stale_error_spend_kept(self):
        tmp = mktmp(self); prev = self._prev(tmp)
        p, n, used = self._cmp_run(tmp, "2026-10-19", fail_at=1)
        self.assertEqual((p["status"], n, used), ("stale_error", 8, 16))   # 8 errored calls billed at the estimate
        doc, _, _ = self._publish(tmp, p, prev)
        self.assertEqual(doc["tables"]["search"], prev["tables"]["search"])   # complete-but-all-errors never shown as 0%

    def test_first_ever_partial_run_is_omitted(self):
        tmp = mktmp(self)
        p, n, used = self._cmp_run(tmp, "2026-10-19", quota=3)
        self.assertEqual((p["status"], used), ("stale_quota", 3))
        self.assertIn("exa", CFG["month1_free_tier_providers"])
        doc, skips, s = self._publish(tmp, p, {"tables": {"search": [], "scrape": [], "browser": []}, "freshness": []})
        self.assertEqual((doc["tables"]["search"], doc["freshness"], doc["not_run_yet"], skips), ([], [], [], []))
        self.assertFalse(doc["has_results"])
        self.assertTrue(s["providers"][0]["reason"].endswith("no previous row, not shown")); self.assertNotIn("previous row kept", s["providers"][0]["reason"])

    def test_full_set_run_still_publishes(self):
        tmp = mktmp(self); prev = self._prev(tmp)
        p, n, used = self._cmp_run(tmp, "2026-10-19")
        self.assertEqual((p["status"], n, used), ("ran", 8, 16))
        doc, skips, _ = self._publish(tmp, p, prev)
        r = doc["tables"]["search"][0]
        self.assertEqual((r["run_date"], r["attempted"]["v"], r["partial"]), ("2026-10-19", 8, False))
        self.assertNotIn("run_status", read_jsonl(tmp / "runs" / "2026-10-19" / "exa" / "search_evergreen.jsonl")[0])

    def _bill(self, exc_at, exc, n_rows=8, ledger=None):
        tmp = mktmp(self); led = tmp / "quota.json"
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

    def _live(self, pid, kind, urlopen):
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        tmp = mktmp(self); led = tmp / "quota.json"
        prov = [{"id": pid, "key_env": "K", "categories": [kind]}]
        pr = {pid: {kind: {"model": "credits", "free_tier": {"units_per_month": 10**6, "needs_card": False}}}}
        sets = {"search_evergreen": {"rows": self.ROWS[:1], "sha": "x"}} if kind == "search" else {"browser_tasks": {"rows": [{"id": "b1", "start_url": "https://a.example/", "timeout_s": 30}], "sha": "x"}}
        with mock.patch("urllib.request.urlopen", urlopen):
            runner.run(providers=prov, sets=sets, pricing=pr, tos={}, adapters=B.ADAPTERS, cfg={}, runs_dir=tmp / "runs", ledger_path=led, run_date="2026-10-19", env={"K": "d"}, mock=True)
        return json.loads(led.read_text())["used"].get(pid, 0) if led.exists() else 0

    @staticmethod
    def _resp(status=200, body=b"{}", exc=None):
        class R:
            def __init__(s): s.status = status
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s):
                if exc: raise exc
                return body
        return lambda *a, **k: R()

    @staticmethod
    def _raise(exc):
        def f(*a, **k): raise exc
        return f

    def test_status_line_received_bills_estimate(self):
        import socket, http.client
        for text in (b"Request timed out", b"Connection refused"):
            body = json.dumps({"success": False, "error": text.decode()}).encode()
            self.assertEqual(self._live("firecrawl", "search", self._resp(200, body)), 1, text)
        self.assertEqual(self._live("firecrawl", "search", self._resp(200, json.dumps({"success": False, "error": "TimeoutError: upstream"}).encode())), 1)
        import ssl
        for exc in (ConnectionResetError(104, "Connection reset by peer"), socket.timeout("timed out"), http.client.IncompleteRead(b"ab", 10), ssl.SSLError(1, "bad record mac")):
            self.assertEqual(self._live("exa", "search", self._resp(200, exc=exc)), 1, exc)
            self.assertGreater(self._live("browserless", "browser", self._resp(200, exc=exc)), 0, exc)
        self.assertFalse(runner.is_network_error(ssl.SSLError(1, "bad record mac"))); self.assertFalse(runner.is_network_error(RuntimeError("SSLError: bad record mac")))

    def test_no_status_line_bills_zero(self):
        import ssl, socket, urllib.error
        for exc in (urllib.error.URLError(ssl.SSLError(1, "handshake failure")), ConnectionRefusedError(111, "Connection refused"), socket.timeout("timed out")):
            self.assertEqual(self._live("exa", "search", self._raise(exc)), 0, exc)
            self.assertEqual(self._live("browserless", "browser", self._raise(exc)), 0, exc)

    def test_urlerror_wrapping_httperror_bills_by_status(self):
        import io, urllib.error
        for code, units in ((401, 0), (403, 1), (500, 1)):
            exc = urllib.error.URLError(urllib.error.HTTPError("https://x.example", code, "x", {}, io.BytesIO(b"")))
            p, lines, led = self._bill({8}, exc)
            self.assertEqual(lines[-1]["billed_units"], units, code)

    def test_runner_short_set_is_stale_error(self):   # kills NEW1 (short-set check) and NEW4 (off-by-one): runner, not tables
        for at in ({8}, {1}, {3, 5}):
            p, lines, _ = self._bill(at, ConnectionRefusedError(111, "Connection refused"))
            k = 8 - len(at)
            self.assertEqual(p["status"], "stale_error", at); self.assertEqual(p["data_calls"], k)
            self.assertIn(f"incomplete set: search_evergreen {k}/8", p["reason"])
        p, _, _ = self._bill(set(), None)
        self.assertEqual(p["status"], "ran")   # full set: not stale

    def test_stale_reasons(self):
        self.assertEqual(runner.STALE_REASONS, ("quota", "timeout", "auth_error", "error", "budget"))
        with self.assertRaises(AssertionError): runner.stale("partial", {"calls": 0, "data_calls": 0}, "x")

    def test_partial_counts_only_scored_or_tos_lines(self):
        mk = lambda i, st: (i, {"item_id": f"q{i}", "status": st, "set_rows": 3})
        self.assertFalse(tables.is_partial([mk(0, "pass"), mk(1, "fail"), mk(2, "skipped_tos")]))
        self.assertTrue(tables.is_partial([mk(0, "pass"), mk(1, "fail"), mk(2, "auth_error")]))
        self.assertTrue(tables.is_partial([mk(0, "pass"), mk(1, "fail"), mk(2, "weird")]))
        tmp = mktmp(self)
        self._guard_run(tmp, "2026-10-19", n_rows=8)
        f = tmp / "runs" / "2026-10-19" / "exa" / "search_evergreen.jsonl"; ls = read_jsonl(f)
        f.write_text("".join(json.dumps(l) + "\n" for l in ls[:5]))   # CEO 2026-10-19: an incomplete log is never published
        self.assertEqual(tables.build(tmp / "runs", self.PRICING)["tables"]["search"], [])

    def test_complete_set_is_not_partial(self):
        tmp = mktmp(self)
        s, n = self._guard_run(tmp, "2026-10-19")
        self.assertEqual((s["providers"][0]["status"], n), ("ran", 8))
        r = tables.build(tmp / "runs", self.PRICING)["tables"]["search"][0]
        self.assertEqual((r["partial"], r["attempted"]["v"], r["passed"]["v"]), (False, 8, 4))
        self.assertNotIn("auth_error", r)

    def test_no_resource_warning(self):   # runner/tables close every file (with open); run the guard tests with ResourceWarning as error
        tests = [f"tests/test_bench.py::Rulings3::{t}" for t in ("test_late_401_previous_row_byte_identical", "test_late_401_keeps_data_rows_and_spend", "test_complete_set_is_not_partial")]
        r = subprocess.run([sys.executable, "-X", "dev", "-W", "error::ResourceWarning", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-W", "error::ResourceWarning",
                            f"--basetemp={mktmp(self)}", *tests], cwd=BENCH, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:]); self.assertIn("3 passed", r.stdout); self.assertNotIn("ResourceWarning", r.stdout + r.stderr)

    # ---------- PR #2 follow-up: stale providers never surface; stale-only runs leave latest.json alone ----------
    def _row(self, pid, v): return {"provider": pid, "set": "news_2026-42", "run_date": v, "attempted": {"v": 1, "src": []}}
    def _fr(self, pid, v): return {"provider": pid, "set": "news_2026-42", "run_date": v, "fresh_hit_at_10": {"v": 0.5, "src": []}}

    def test_stale_provider_not_in_skips_or_not_run_yet(self):   # kills R1, and the not_run_yet bug (evidence case4)
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        tmp = mktmp(self); prev = tmp / "latest.json"
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
        tmp = mktmp(self); prev = tmp / "latest.json"; old = self._fr("exa", "2026-10-12")
        prev.write_text(json.dumps({"tables": {"search": [self._row("exa", "2026-10-12")]}, "freshness": [old, self._fr("tavily", "2026-10-12")]}, indent=1))
        doc = {"tables": {"search": [self._row("exa", "2026-10-19"), self._row("tavily", "2026-10-19")]},
               "freshness": [self._fr("exa", "2026-10-19"), self._fr("tavily", "2026-10-19")]}
        B.keep_previous_rows(doc, prev, {"exa"})
        self.assertEqual(sorted((r["provider"], r["run_date"]) for r in doc["freshness"]), [("exa", "2026-10-12"), ("tavily", "2026-10-19")])
        self.assertEqual(sorted((r["provider"], r["run_date"]) for r in doc["tables"]["search"]), [("exa", "2026-10-12"), ("tavily", "2026-10-19")])

    def test_stale_without_previous_row_not_published_from_older_log(self):   # kills R3
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        tmp = mktmp(self)
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
        root = mktmp(self) / "site"
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
        self.assertEqual(json.loads(led.read_text())["used"]["exa"] - used0, 2 * 1)
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

class BrightDataBudget(unittest.TestCase):
    PROV = [{"id": "brightdata", "categories": ["scrape"], "key_env": "BRIGHTDATA_API_KEY"}]
    PRICING = {"brightdata": {"scrape": {"model": "per_request", "free_tier": {"units_per_month": 5000, "needs_card": False, "pool": "brightdata"}}}}
    OCT = dt.datetime(2026, 10, 19, 12, tzinfo=dt.timezone.utc)

    def setUp(self): self.tmp = mktmp(self); self.calls = []; (self.tmp / "spend").mkdir()

    def _rows(self, n): return [{"id": f"b{i}", "url": f"https://b{i}.example/", "class": "static", "marker": {"type": "text_contains", "value": "hello world"}, "min_chars": 5} for i in range(n)]

    def _ok(self, key, item): self.calls.append(item["id"]); return {"http_status": 200, "billed_units": 1, "text": "hello world page"}

    def _seed(self, month, requests, usd):
        f = self.tmp / "spend" / f"brightdata-{month}.json"; f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"provider": "brightdata", "month": month, "usd_per_1k": 1.5, "requests": requests, "est_usd": usd})); return f

    def _spend(self, month="2026-10"):
        f = self.tmp / "spend" / f"brightdata-{month}.json"; return json.loads(f.read_text()) if f.exists() else None

    def _run(self, n, fn=None, now=None, env=None, tag="", run_date=None, providers=None, adapters=None, pricing=None):
        s = runner.run(providers=providers or self.PROV, sets={"scrape_urls": {"rows": self._rows(n), "sha": "x"}}, pricing=pricing or self.PRICING, tos={},
                       adapters=adapters or {"brightdata": {"scrape": fn or self._ok}}, cfg={"spend_budget": CFG["spend_budget"], "challenge_signatures": []},
                       runs_dir=self.tmp / f"runs{tag}", ledger_path=self.tmp / f"quota{tag}.json", run_date=run_date or (now or self.OCT).date().isoformat(),
                       env={"BRIGHTDATA_API_KEY": "d"} if env is None else env, mock=True, now_fn=lambda: now or self.OCT, spend_dir=self.tmp / "spend")
        return s["providers"][0] if providers is None else s["providers"]

    def test_config_budget_values(self):
        self.assertEqual({k: CFG["spend_budget"]["brightdata"][k] for k in ("usd_per_1k", "stop_usd", "cap_usd")}, {"usd_per_1k": 1.5, "stop_usd": 4.0, "cap_usd": 5.0})

    def test_under_cap_runs_and_creates_spend_file(self):
        self.assertIsNone(self._spend())
        p = self._run(5)
        self.assertEqual((p["status"], p["calls"]), ("ran", 5))
        self.assertEqual({k: self._spend()[k] for k in ("month", "requests", "est_usd")}, {"month": "2026-10", "requests": 5, "est_usd": 0.0075})

    def test_spend_file_incremented_across_runs(self):
        self._seed("2026-10", 100, 0.15); p = self._run(4)
        self.assertEqual(p["status"], "ran"); self.assertEqual((self._spend()["requests"], self._spend()["est_usd"]), (104, 0.156))

    def test_at_exactly_4_usd_is_stale_budget(self):
        f = self._seed("2026-10", 2667, 4.0005); before = f.read_text(); p = self._run(3)
        self.assertEqual((p["status"], p["calls"], self.calls), ("stale_budget", 0, [])); self.assertEqual(f.read_text(), before)
        self.assertIn("would exceed the $4.00 stop", p["reason"])

    def test_budget_blocked_exact_4_usd_boundary(self):
        sf = runner.SpendFile(self.tmp / "spend", "brightdata", "2026-10", 1.5); sf.release()
        for mtd, blocked in ((4.0, True), (3.9985, False), (3.999, True), (3.9975, False), (4.0005, True), (0.0, False)):
            sf.d["est_usd"] = mtd; self.assertEqual(runner.budget_blocked(sf, 1, 4.0), blocked, mtd)

    def test_last_request_under_4_usd_allowed_then_stale_budget(self):
        self._seed("2026-10", 2665, 3.9975); p = self._run(3)
        self.assertEqual((p["status"], p["calls"]), ("stale_budget", 1)); self.assertEqual((self._spend()["requests"], self._spend()["est_usd"]), (2666, 3.999))

    def test_next_request_crossing_4_usd_is_stale_budget(self):
        self._seed("2026-10", 2666, 3.999); p = self._run(3)
        self.assertEqual((p["status"], p["calls"], self.calls), ("stale_budget", 0, [])); self.assertEqual(self._spend()["requests"], 2666)

    def test_budget_stops_mid_run(self):
        self._seed("2026-10", 2660, 3.99); p = self._run(10)
        self.assertEqual((p["status"], p["calls"], len(self.calls)), ("stale_budget", 6, 6)); self.assertEqual(self._spend()["est_usd"], 3.999)
        lines = read_jsonl(self.tmp / "runs" / "2026-10-19" / "brightdata" / "scrape_urls.jsonl")
        self.assertEqual((len(lines), {l["run_status"] for l in lines}), (6, {"stale_budget"}))

    def test_month_rollover_starts_new_file(self):
        oct_f = self._seed("2026-10", 2667, 4.0005); before = oct_f.read_text()
        p = self._run(2, now=dt.datetime(2026, 11, 1, 1, tzinfo=dt.timezone.utc))
        self.assertEqual(p["status"], "ran"); self.assertEqual(json.loads(oct_f.read_text()), json.loads(before) | {"rolled_over_to": "2026-11"})
        self.assertEqual({k: self._spend("2026-11")[k] for k in ("month", "requests", "est_usd")}, {"month": "2026-11", "requests": 2, "est_usd": 0.003})

    def test_429_bills_and_increments_spend(self):
        def fn(key, item):
            self.calls.append(1)
            if len(self.calls) <= 2: raise RuntimeError('HTTP 429: {"error":"rate limited"}')
            return {"http_status": 200, "billed_units": 1, "text": "hello world page"}
        p = self._run(5, fn)
        self.assertEqual((p["status"], p["calls"]), ("stale_quota", 5)); self.assertEqual(self._spend()["requests"], 5)

    def test_status_line_billing_paths(self):
        import urllib.error, socket
        cases = [(lambda: urllib.error.URLError(socket.gaierror(-2, "Name or service not known")), 0, "stale_error"),
                 (lambda: TimeoutError("timed out"), 0, "stale_error"),
                 (lambda: RuntimeError("HTTP 401: bad key"), 0, "stale_auth_error"),
                 (lambda: RuntimeError("HTTP 403: forbidden"), 1, "stale_auth_error"),
                 (lambda: RuntimeError("HTTP 502: bad gateway"), 3, "stale_error")]
        for mk, want, st in cases:
            self.setUp()
            def fn(key, item, mk=mk): raise mk()
            p = self._run(3, fn)
            sp = self._spend()
            self.assertEqual((p["status"], sp and sp["requests"]), (st, want), st)

    def test_no_key_is_skipped_no_key(self):
        p = self._run(3, env={})
        self.assertEqual((p["status"], self.calls, self._spend()), ("skipped_no_key", [], None))

    def test_stale_budget_keeps_previous_row(self):
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        prev = self.tmp / "latest.json"; row = {"provider": "brightdata", "attempted": {"v": 10}, "run_date": "2026-10-12"}
        prev.write_text(json.dumps({"tables": {"scrape": [row]}, "freshness": []}))
        doc = {"tables": {"scrape": [{"provider": "brightdata", "attempted": {"v": 2}}]}, "freshness": []}
        s = {"providers": [{"provider": "brightdata", "category": "scrape", "status": "stale_budget", "reason": "x; not published"}]}
        self.assertEqual(B.keep_previous_rows(doc, prev, B.stale_providers(s)), {"brightdata"}); self.assertEqual(doc["tables"]["scrape"], [row])

    def test_mock_spend_dir_refuses_repo_spend(self):
        with self.assertRaises(RuntimeError):
            runner.run(providers=self.PROV, sets={}, pricing=self.PRICING, tos={}, adapters={}, cfg={}, runs_dir=self.tmp / "runs",
                       ledger_path=self.tmp / "q.json", run_date="2026-10-19", env={}, mock=True, spend_dir=BENCH / "spend")

    def test_concurrent_runs_cannot_pass_stop(self):
        import threading, time as _t
        self._seed("2026-10", 2646, 3.969); lock = threading.Lock(); n = []
        def slow(key, item):
            with lock: n.append(1)
            _t.sleep(0.01); return {"http_status": 200, "billed_units": 1, "text": "hello world page"}
        out = {}
        th = [threading.Thread(target=lambda t=t: out.__setitem__(t, self._run(20, slow, tag=t))) for t in ("a", "b")]
        [x.start() for x in th]; [x.join() for x in th]
        self.assertEqual((self._spend()["requests"], self._spend()["est_usd"], len(n)), (2666, 3.999, 20))
        self.assertEqual(sum(p["calls"] for p in out.values()), 20); self.assertEqual({p["status"] for p in out.values()}, {"stale_budget"})

    def test_concurrent_runs_total_correct_under_cap(self):
        import threading
        out = {}
        th = [threading.Thread(target=lambda t=t: out.__setitem__(t, self._run(25, tag=t))) for t in ("a", "b", "c")]
        [x.start() for x in th]; [x.join() for x in th]
        self.assertEqual({p["status"] for p in out.values()}, {"ran"}); self.assertEqual((self._spend()["requests"], self._spend()["est_usd"]), (75, 0.1125))

    def test_corrupt_or_empty_spend_file_fails_closed_others_run(self):
        prov = self.PROV + [{"id": "exa", "categories": ["scrape"], "key_env": "EXA_API_KEY"}]
        pricing = self.PRICING | {"exa": {"scrape": {"model": "per_request", "free_tier": {"units_per_month": 5000, "needs_card": False}}}}
        for content in ("", "{not json", "[]", '{"month": "2026-10", "requests": "x", "est_usd": 0}', '{"month": "2026-09", "requests": 1, "est_usd": 0.0015}'):
            self.setUp(); f = self.tmp / "spend" / "brightdata-2026-10.json"; f.write_text(content)
            ps = self._run(3, providers=prov, adapters={"brightdata": {"scrape": self._ok}, "exa": {"scrape": self._ok}}, pricing=pricing,
                           env={"BRIGHTDATA_API_KEY": "d", "EXA_API_KEY": "d"})
            st = {p["provider"]: p for p in ps}
            self.assertEqual((st["brightdata"]["status"], st["brightdata"]["calls"], st["exa"]["status"], st["exa"]["calls"]), ("stale_budget", 0, "ran", 3), content)
            self.assertIn("empty, corrupt or unreadable", st["brightdata"]["reason"]); self.assertIn("failing closed", st["brightdata"]["reason"])
            self.assertEqual(f.read_text(), content); self.assertEqual(len(self.calls), 3)

    def test_bad_numbers_in_spend_file_fail_closed(self):
        bad = ["NaN", "Infinity", "-Infinity", "true", "false", '"0.5"', "-0.0015", "null"]
        for field in ("est_usd", "requests"):
            for v in bad + (["1.0", "1e400"] if field == "requests" else ["1e400"]):
                self.setUp(); f = self.tmp / "spend" / "brightdata-2026-10.json"
                d = {"provider": "brightdata", "month": "2026-10", "usd_per_1k": 1.5, "requests": 1, "est_usd": "0.0015"}
                txt = json.dumps(d).replace('"0.0015"', "0.0015")
                txt = txt.replace('"requests": 1', f'"requests": {v}') if field == "requests" else txt.replace('"est_usd": 0.0015', f'"est_usd": {v}')
                f.write_text(txt); p = self._run(2)
                self.assertEqual((p["status"], p["calls"], self.calls), ("stale_budget", 0, []), (field, v))
                self.assertIn("failing closed", p["reason"]); self.assertEqual(f.read_text(), txt)
        for v in ("0", "0.0", "3.9975"):
            self.setUp(); self._seed("2026-10", 0, json.loads(v)); self.assertEqual(self._run(1)["status"], "ran", v)

    def test_negative_requests_in_spend_file_fails_closed(self):
        for v in (-1, -2667):
            self.setUp(); f = self.tmp / "spend" / "brightdata-2026-10.json"
            txt = json.dumps({"provider": "brightdata", "month": "2026-10", "usd_per_1k": 1.5, "requests": v, "est_usd": 0.0015}); f.write_text(txt)
            p = self._run(2)
            self.assertEqual((p["status"], p["calls"], self.calls), ("stale_budget", 0, []), v)
            self.assertIn("empty, corrupt or unreadable", p["reason"]); self.assertIn("failing closed", p["reason"]); self.assertEqual(f.read_text(), txt)

    def test_spend_write_is_atomic(self):
        import pathlib
        f = self._seed("2026-10", 10, 0.015); old = f.read_text()
        def crash(*a, **k): raise OSError("simulated crash before rename")
        with mock.patch("os.replace", crash), self.assertRaises(OSError): self._run(1)
        self.assertEqual(f.read_text(), old); self.assertEqual(json.loads(f.read_text())["requests"], 10)
        real = pathlib.Path.write_text
        def partial(path, data, *a, **k):
            if path.name.startswith("brightdata-"):
                with open(path, "w") as fh: fh.write(data[:7])
                raise OSError("simulated crash mid-write")
            return real(path, data, *a, **k)
        def partial_open(file, mode="r", *a, **k):
            fh = open_real(file, mode, *a, **k)
            if Path(file).name == "brightdata-2026-10.json" and any(c in mode for c in "wa+"):
                fh.truncate(0); fh.close(); raise OSError("simulated crash after truncate")
            return fh
        import builtins; open_real = builtins.open
        with mock.patch.object(pathlib.Path, "write_text", partial), mock.patch("builtins.open", partial_open), self.assertRaises(OSError): self._run(1)
        self.assertEqual(f.read_text(), old); self.assertEqual(self._run(1)["status"], "ran"); self.assertEqual(self._spend()["requests"], 11)

    def test_rule6_first_ever_month_seeds(self):
        self.assertFalse(any((self.tmp / "spend").glob("*.json")))
        p = self._run(2); self.assertEqual((p["status"], self._spend()["requests"], self._spend()["est_usd"]), ("ran", 2, 0.003))
        self.assertNotIn("rolled_over_to", self._spend())

    def _mixed(self, n=3, now=None):
        prov = self.PROV + [{"id": "exa", "categories": ["scrape"], "key_env": "EXA_API_KEY"}]
        pricing = self.PRICING | {"exa": {"scrape": {"model": "per_request", "free_tier": {"units_per_month": 5000, "needs_card": False}}}}
        ps = self._run(n, providers=prov, adapters={"brightdata": {"scrape": self._ok}, "exa": {"scrape": lambda k, i: {"http_status": 200, "billed_units": 1, "text": "hello world page"}}},
                       pricing=pricing, env={"BRIGHTDATA_API_KEY": "d", "EXA_API_KEY": "d"}, now=now)
        return {p["provider"]: p for p in ps}

    def _refused(self, st, why):
        self.assertEqual((st["brightdata"]["status"], st["brightdata"]["calls"], self.calls), ("stale_budget", 0, []))
        self.assertIn("failing closed (rule 6)", st["brightdata"]["reason"]); self.assertIn(why, st["brightdata"]["reason"])
        self.assertEqual((st["exa"]["status"], st["exa"]["calls"]), ("ran", 3)); self.assertIsNone(self._spend())

    def test_rule6_deleted_mid_month_refuses(self):
        self._seed("2026-09", 50, 0.075); self._run(2)
        self.assertEqual(self._spend()["requests"], 2); self.calls.clear()
        (self.tmp / "spend" / "brightdata-2026-10.json").unlink()
        self._refused(self._mixed(), "already rolled over to 2026-10")

    def test_rule6_new_month_with_last_month_present_seeds(self):
        sep = self._seed("2026-09", 2666, 3.999); st = self._mixed()
        self.assertEqual((st["brightdata"]["status"], st["brightdata"]["calls"], st["exa"]["status"]), ("ran", 3, "ran"))
        self.assertEqual((self._spend()["requests"], self._spend()["est_usd"]), (3, 0.0045))
        self.assertEqual(json.loads(sep.read_text())["rolled_over_to"], "2026-10"); self.assertEqual(json.loads(sep.read_text())["requests"], 2666)

    def test_rule6_january_rolls_from_december(self):
        dec = self._seed("2026-12", 5, 0.0075)
        p = self._run(1, now=dt.datetime(2027, 1, 1, 6, tzinfo=dt.timezone.utc))
        self.assertEqual((p["status"], self._spend("2027-01")["requests"], json.loads(dec.read_text())["rolled_over_to"]), ("ran", 1, "2027-01"))

    def test_rule6_gap_month_refuses(self):
        self._seed("2026-08", 10, 0.015); self._refused(self._mixed(), "previous month 2026-09 is absent")

    def test_rule6_other_files_but_no_previous_refuses(self):
        self._seed("2026-11", 1, 0.0015); f = self.tmp / "spend" / "brightdata-2026-10.json"
        st = self._mixed(); self.assertEqual((st["brightdata"]["status"], st["exa"]["status"]), ("stale_budget", "ran"))
        self.assertIn("later month file exists", st["brightdata"]["reason"]); self.assertFalse(f.exists())

    def test_rule6_second_seed_after_deletion_refuses(self):
        self._seed("2026-09", 1, 0.0015); self.assertEqual(self._run(1)["status"], "ran")
        for _ in range(2):
            (self.tmp / "spend" / "brightdata-2026-10.json").unlink(missing_ok=True); self.calls.clear()
            self._refused(self._mixed(), "this month's file was deleted")

    def test_rule6_corrupt_or_foreign_previous_month_refuses(self):
        for txt in ("", "{bad", '{"month": "2026-08"}', '{"month": "2026-09", "rolled_over_to": "2026-11"}'):
            self.setUp(); (self.tmp / "spend" / "brightdata-2026-09.json").write_text(txt)
            st = self._mixed(); self.assertEqual((st["brightdata"]["status"], st["brightdata"]["calls"], st["exa"]["calls"]), ("stale_budget", 0, 3), txt)
            self.assertIsNone(self._spend()); self.assertEqual((self.tmp / "spend" / "brightdata-2026-09.json").read_text(), txt)

    def test_rule6_invalid_previous_month_refuses(self):
        good = {"provider": "brightdata", "month": "2026-09", "usd_per_1k": 1.5, "requests": 4, "est_usd": 0.006}
        for bad in ({"provider": "exa"}, {"est_usd": "NaN"}, {"requests": True}, {"requests": False}, {"est_usd": True}, {"est_usd": "Infinity"},
                    {"requests": -1}, {"requests": 1.0}, {"provider": None}, {"rolled_over_to": 202610}, {"rolled_over_to": "2026-08"}):
            self.setUp(); f = self.tmp / "spend" / "brightdata-2026-09.json"
            txt = json.dumps(good | bad).replace('"NaN"', "NaN").replace('"Infinity"', "Infinity"); f.write_text(txt)
            st = self._mixed(); self.assertEqual((st["brightdata"]["status"], st["brightdata"]["calls"], st["exa"]["calls"]), ("stale_budget", 0, 3), bad)
            self.assertIn("unreadable, corrupt or invalid", st["brightdata"]["reason"]); self.assertEqual(f.read_text(), txt); self.assertIsNone(self._spend())
        self.setUp(); self._seed("2026-09", 4, 0.006); self.assertEqual(self._mixed()["brightdata"]["status"], "ran")
        self.setUp(); f = self.tmp / "spend" / "brightdata-2026-10.json"
        txt = json.dumps({"provider": "brightdata", "month": "2026-10", "usd_per_1k": 1.5, "requests": 4, "est_usd": 0.006, "rolled_over_to": "2026-12"}); f.write_text(txt)
        st = self._mixed(now=dt.datetime(2026, 11, 2, 12, tzinfo=dt.timezone.utc))
        self.assertEqual((st["brightdata"]["status"], st["brightdata"]["calls"], self.calls, st["exa"]["status"], st["exa"]["calls"]), ("stale_budget", 0, [], "ran", 3))
        self.assertIn("rolled over to 2026-12, not 2026-11", st["brightdata"]["reason"]); self.assertEqual(f.read_text(), txt); self.assertIsNone(self._spend("2026-11"))

    def test_rule6_current_month_file_full_validation(self):
        for bad in ({"provider": "exa"}, {"rolled_over_to": 5}):
            self.setUp(); f = self.tmp / "spend" / "brightdata-2026-10.json"
            f.write_text(json.dumps({"provider": "brightdata", "month": "2026-10", "usd_per_1k": 1.5, "requests": 1, "est_usd": 0.0015} | bad))
            p = self._run(1); self.assertEqual((p["status"], p["calls"]), ("stale_budget", 0), bad)

    def test_rule6_crash_after_marker_refuses_then_documented_recovery(self):
        sep = self._seed("2026-09", 7, 0.0105)
        real = runner.SpendFile._write
        def crash(path, d):
            if path.name == "brightdata-2026-10.json": raise OSError("simulated crash after marker")
            return real(path, d)
        with mock.patch.object(runner.SpendFile, "_write", staticmethod(crash)): p = self._run(1)
        self.assertEqual((p["status"], p["calls"]), ("stale_budget", 0)); self.assertEqual(json.loads(sep.read_text())["rolled_over_to"], "2026-10"); self.assertIsNone(self._spend())
        p = self._run(1); self.assertEqual((p["status"], p["calls"], self.calls), ("stale_budget", 0, [])); self.assertIn("this month's file was deleted", p["reason"])
        nov = dt.datetime(2026, 11, 2, 12, tzinfo=dt.timezone.utc)
        p = self._run(1, now=nov); self.assertEqual((p["status"], p["calls"]), ("stale_budget", 0)); self.assertIn("previous month 2026-10 is absent", p["reason"])
        n = 0
        rec = {"provider": "brightdata", "month": "2026-10", "usd_per_1k": 1.5, "requests": n, "est_usd": round(n * 1.5 / 1000, 6)}
        (self.tmp / "spend" / "brightdata-2026-10.json").write_text(json.dumps(rec, indent=1, sort_keys=True))
        p = self._run(2); self.assertEqual((p["status"], self._spend()["requests"]), ("ran", 2))
        p = self._run(1, now=nov); self.assertEqual((p["status"], self._spend("2026-11")["requests"]), ("ran", 1))
        self.assertEqual(self._spend("2026-10")["rolled_over_to"], "2026-11"); self.assertEqual(json.loads(sep.read_text())["requests"], 7)

    def test_rule6_cross_month_runs_serialize(self):
        import threading, time as _t
        self._seed("2026-10", 5, 0.0075); gate = threading.Event(); started = threading.Event(); order = []
        def oct_fn(key, item):
            order.append(("oct", item["id"])); started.set(); gate.wait(5)
            return {"http_status": 200, "billed_units": 1, "text": "hello world page"}
        def nov_fn(key, item):
            order.append(("nov", item["id"])); return {"http_status": 200, "billed_units": 1, "text": "hello world page"}
        out = {}
        a = threading.Thread(target=lambda: out.__setitem__("oct", self._run(1, oct_fn, tag="o")))
        b = threading.Thread(target=lambda: out.__setitem__("nov", self._run(2, nov_fn, tag="n", now=dt.datetime(2026, 11, 1, 1, tzinfo=dt.timezone.utc))))
        a.start(); self.assertTrue(started.wait(5)); b.start(); _t.sleep(0.3)
        self.assertEqual([o[0] for o in order], ["oct"]); self.assertIsNone(self._spend("2026-11"))
        gate.set(); a.join(); b.join()
        self.assertEqual([o[0] for o in order], ["oct", "nov", "nov"]); self.assertEqual({p["status"] for p in out.values()}, {"ran"})
        o = self._spend("2026-10"); self.assertEqual((o["requests"], o["est_usd"], o.get("rolled_over_to")), (6, 0.009, "2026-11"))
        self.assertEqual((self._spend("2026-11")["requests"], self._spend("2026-11")["est_usd"]), (2, 0.003))

    def test_rule6_marker_written_atomically_before_seed(self):
        sep = self._seed("2026-09", 7, 0.0105); old = sep.read_text()
        def crash(*a, **k): raise OSError("simulated crash before rename")
        with mock.patch("os.replace", crash): p = self._run(1)
        self.assertEqual((p["status"], p["calls"], self.calls), ("stale_budget", 0, [])); self.assertIn("failing closed", p["reason"])
        self.assertEqual(sep.read_text(), old); self.assertIsNone(self._spend())
        self.assertEqual(self._run(1)["status"], "ran"); self.assertEqual(json.loads(sep.read_text())["rolled_over_to"], "2026-10")

    def test_missing_or_unreadable_spend_dir_fails_closed(self):
        (self.tmp / "spend").rmdir(); p = self._run(3)
        self.assertEqual((p["status"], p["calls"], self.calls), ("stale_budget", 0, [])); self.assertIn("missing or unreadable", p["reason"])
        self.assertFalse((self.tmp / "spend").exists())
        self.setUp(); real = os.access
        with mock.patch("os.access", lambda path, mode, **k: False if Path(path) == self.tmp / "spend" else real(path, mode, **k)):
            p = self._run(3)
        self.assertEqual((p["status"], p["calls"], self.calls), ("stale_budget", 0, [])); self.assertIn("missing or unreadable", p["reason"])

    def test_live_spend_dir_exists_in_repo(self):
        self.assertTrue((BENCH / "spend").is_dir())

    def test_month_from_utc_timestamp_not_run_date(self):
        et = dt.timezone(dt.timedelta(hours=-4))
        p = self._run(2, now=dt.datetime(2026, 10, 31, 21, 30, tzinfo=et), run_date="2026-10-31")
        self.assertEqual(p["status"], "ran"); self.assertIsNone(self._spend("2026-10")); self.assertEqual(self._spend("2026-11")["requests"], 2)
        self.setUp(); self._run(1, now=dt.datetime(2026, 10, 31, 19, 59, tzinfo=et), run_date="2026-11-01")
        self.assertIsNone(self._spend("2026-11")); self.assertEqual(self._spend("2026-10")["requests"], 1)
        self.assertEqual(runner.spend_month("2026-10-31T21:30:00-04:00"), "2026-11"); self.assertEqual(runner.spend_month("2026-10-31T23:59:59+00:00"), "2026-10")

    def test_stale_budget_not_in_not_run_yet(self):
        s = {"providers": [{"provider": "brightdata", "category": "scrape", "status": "stale_budget", "reason": "x"},
                           {"provider": "apify", "category": "scrape", "status": "skipped_no_key", "reason": "not run (no key)"}]}
        nr = tables.not_run_yet(s, {"month1_free_tier_providers": ["brightdata", "apify"]}, {"tables": {"scrape": []}})
        self.assertEqual([n["provider"] for n in nr], ["apify"])

    def test_adapter_http_error_mapping_billing(self):
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B, urllib.error, io, socket
        cases = [(lambda: urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b"no")), "auth_error", 1, "stale_auth_error"),
                 (lambda: urllib.error.HTTPError("u", 429, "Too Many", {}, io.BytesIO(b"slow")), "fail", 1, None),
                 (lambda: urllib.error.HTTPError("u", 500, "Err", {}, io.BytesIO(b"boom")), "fail", 1, None),
                 (lambda: urllib.error.HTTPError("u", 401, "Unauth", {}, io.BytesIO(b"key")), "auth_error", 0, "stale_auth_error"),
                 (lambda: urllib.error.URLError(socket.gaierror(-2, "Name or service not known")), "unbilled_no_response", 0, "stale_error")]
        for mk, line_st, units, prov_st in cases:
            self.setUp()
            def boom(req, timeout=None, mk=mk): raise mk()
            with mock.patch.dict(os.environ, {"BRIGHTDATA_ZONE": "awi_unlocker"}), mock.patch("urllib.request.urlopen", boom):
                p = self._run(1, fn=B._brightdata_scrape)
            log = self.tmp / "runs" / "2026-10-19" / "brightdata" / "scrape_urls.jsonl"
            if units:
                line = read_jsonl(log)[0]; self.assertEqual((line["status"], line["billed_units"]), (line_st, units), line_st)
            else: self.assertFalse(log.exists(), line_st)
            self.assertEqual(p["calls"], 1, line_st)
            self.assertEqual((self._spend() or {"requests": 0})["requests"], units, line_st)
            if prov_st: self.assertEqual(p["status"], prov_st, line_st)

    def test_adapter_request_mocked(self):
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        seen = {}
        class R:
            status = 200
            def read(s): return b"<html><body><p>hello world</p><script>x</script></body></html>"
            def __enter__(s): return s
            def __exit__(s, *a): return False
        def fake(req, timeout=None): seen.update(url=req.full_url, body=json.loads(req.data), auth=req.get_header("Authorization")); return R()
        with mock.patch.dict(os.environ, {"BRIGHTDATA_ZONE": "awi_unlocker"}), mock.patch("urllib.request.urlopen", fake):
            out = B._brightdata_scrape("k", {"url": "https://a.example/"})
        self.assertEqual(seen, {"url": "https://api.brightdata.com/request", "body": {"zone": "awi_unlocker", "url": "https://a.example/", "format": "raw"}, "auth": "Bearer k"})
        self.assertEqual((out["http_status"], out["billed_units"]), (200, 1)); self.assertIn("hello world", out["text"]); self.assertNotIn("x", out["text"].replace("hello world", ""))
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            self.assertEqual(B._brightdata_scrape("k", {"url": "https://a.example/"})["billed_units"], 0)
        self.assertIn("brightdata", B.ADAPTERS)


class ChallengeInterstitial(unittest.TestCase):
    PAGE = (Path(__file__).parent / "fixtures" / "challenge" / "nature_client_challenge.html").read_text()
    ROW = {"id": "sc-x", "url": "https://www.nature.com/articles/nature14539", "marker": {"type": "text_contains", "value": "Deep learning"}, "min_chars": 10}
    def sigs(self): return CFG["challenge_signatures"], CFG["challenge_html_signatures"]
    def test_fixture_fails_on_html(self):
        ok, why = checks.scrape_check(self.ROW, {"text": "Deep learning " + freeze.html_text(self.PAGE), "html": self.PAGE}, *self.sigs())
        self.assertFalse(ok); self.assertIn("challenge page signature", why)
    def test_fixture_text_only_fails(self):
        ok, why = checks.scrape_check(self.ROW, {"text": "Deep learning " + freeze.html_text(self.PAGE)}, *self.sigs())
        self.assertFalse(ok); self.assertIn("A required part of this site couldn", why)
    def test_real_article_passes(self):
        html = "<html><head><title>Deep learning | Nature</title></head><body><p>Deep learning allows computational models.</p><a href='/challenge'>x</a></body></html>"
        self.assertEqual(checks.scrape_check(self.ROW, {"text": freeze.html_text(html), "html": html}, *self.sigs()), (True, "marker found, length ok"))
    def test_runner_uses_html_signatures(self):
        ok, why = runner._grade("scrape", "scrape_urls", self.ROW, {"text": "Deep learning everywhere", "html": self.PAGE}, CFG)
        self.assertFalse(ok); self.assertIn("challenge page signature", why)
    def test_freeze_logs_interstitial_failure(self):
        with tmpd(self) as d:
            (Path(d) / "scrape_urls.jsonl").write_text(json.dumps(self.ROW) + "\n"); (Path(d) / "search_evergreen.jsonl").write_text("")
            log = freeze.freeze(d, CFG, lambda u: 200, lambda u: {"text": "Deep learning " + freeze.html_text(self.PAGE), "html": self.PAGE}, "2026-10-19")
        self.assertEqual(len(log["failures"]), 1); self.assertIn("sc-x: marker health check failed (challenge page signature", log["failures"][0])
    def test_html_text_skips_script_and_noscript(self):
        t = freeze.html_text("<p>a</p><script>b()</script><noscript>c</noscript><style>d{}</style><p>e</p>")
        self.assertEqual(t.split(), ["a", "e"])
    def test_chrome_reference_browser_hook(self):
        with tmpd(self) as d:
            fake = Path(d) / "chrome"; seen = Path(d) / "argv"
            fake.write_text(f"#!{sys.executable}\nimport sys\nopen({str(seen)!r}, 'w').write(' '.join(sys.argv[1:]))\nprint('<html><body><p>Deep learning</p><script>x</script></body></html>')\n")
            fake.chmod(0o755)
            out = freeze.chrome_reference_browser("https://www.nature.com/articles/nature14539", binary=str(fake))
            argv = seen.read_text()
        self.assertEqual(out["text"].split(), ["Deep", "learning"]); self.assertIn("<p>Deep learning</p>", out["html"])
        self.assertIn("--headless=new", argv); self.assertIn("--dump-dom https://www.nature.com/articles/nature14539", argv)
        ud = argv.split("--user-data-dir=")[1].split()[0]; self.assertFalse(Path(ud).exists())
    def _live(self, d, script, plain=lambda u, **k: {"status": 403, "text": "", "html": ""}, row=None):
        fake = Path(d) / "chrome"; calls = Path(d) / "calls"; self.sleeps = []; self.plain_calls = []
        fake.write_text(f"#!{sys.executable}\nimport sys\nopen({str(calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\nn = len(open({str(calls)!r}).read().splitlines())\n{script}\n")
        fake.chmod(0o755); sd = Path(d) / "sets"; sd.mkdir()
        (sd / "scrape_urls.jsonl").write_text(json.dumps(row or self.ROW) + "\n"); (sd / "search_evergreen.jsonl").write_text("")
        def pf(u, **k): self.plain_calls.append((u, k.get("ua"))); return plain(u, **k)
        with mock.patch("builtins.print"), mock.patch.object(freeze, "plain_fetch", pf), mock.patch.object(freeze.time, "sleep", self.sleeps.append), mock.patch.object(freeze.time, "monotonic", lambda: 0.0):
            rc = freeze.main(["--live", "--sets", str(sd), "--chrome", str(fake)])
        log = json.loads(next(sd.glob("freeze-*.json")).read_text())
        return rc, log, calls.read_text().splitlines() if calls.exists() else []
    GOOD = "print('<html><body><p>Deep learning works</p></body></html>')"
    BAD = "print(open(" + repr(str(Path(__file__).parent / "fixtures" / "challenge" / "nature_client_challenge.html")) + ").read())"
    def test_live_retry_fail_then_pass_passes(self):
        with tmpd(self) as d: rc, log, calls = self._live(d, f"if n == 1: {self.BAD}\nelse: {self.GOOD}")
        self.assertEqual(rc, 0); self.assertEqual(log["failures"], []); m = log["markers"][0]
        self.assertTrue(m["ok"]); self.assertEqual([a["ok"] for a in m["attempts"]], [False, True]); self.assertIn("challenge page signature", m["attempts"][0]["reason"])
    def test_live_retry_fail_fail_fails(self):
        with tmpd(self) as d: rc, log, calls = self._live(d, self.BAD)
        self.assertEqual(rc, 1); m = log["markers"][0]; self.assertFalse(m["ok"]); self.assertEqual([a["ok"] for a in m["attempts"]], [False, False])
        self.assertEqual(len(log["failures"]), 1); self.assertEqual(log["failures"][0].count("challenge page signature"), 2)
    def test_live_retry_exactly_once_with_harness_ua(self):
        with tmpd(self) as d: rc, log, calls = self._live(d, self.BAD)
        self.assertEqual(len(calls), 2); self.assertIn("AWI-Freeze/1", calls[0]); self.assertIn("--user-agent=AgentWebIndexBot/1.0 ", calls[1]); self.assertNotIn("FactDesk", " ".join(calls))
    def test_live_pass_first_time_no_retry(self):
        with tmpd(self) as d: rc, log, calls = self._live(d, self.GOOD)
        self.assertEqual(rc, 0); self.assertEqual(len(calls), 1); self.assertEqual(len(log["markers"][0]["attempts"]), 1)
    def test_status_retry_once(self):
        with tmpd(self) as d:
            (Path(d) / "search_evergreen.jsonl").write_text(json.dumps({"id": "s1", "source_url": "https://a.example/"}) + "\n"); (Path(d) / "scrape_urls.jsonl").write_text("")
            seen = []
            log = freeze.freeze(d, CFG, lambda u: 403, lambda u: {}, "2026-10-19", lambda u: seen.append(u) or 200, None)
        self.assertEqual(seen, ["https://a.example/"]); self.assertEqual(log["source_url"][0]["attempts"], [403, 200]); self.assertEqual(log["failures"], [])
    def test_status_retry_fail_fail_fails_with_two_attempts(self):
        with tmpd(self) as d:
            (Path(d) / "search_evergreen.jsonl").write_text(json.dumps({"id": "s1", "source_url": "https://a.example/"}) + "\n"); (Path(d) / "scrape_urls.jsonl").write_text("")
            seen = []
            log = freeze.freeze(d, CFG, lambda u: 403, lambda u: {}, "2026-10-19", lambda u: seen.append(u) or 503, None)
        self.assertEqual(len(seen), 1); self.assertEqual(log["source_url"][0]["attempts"], [403, 503]); self.assertNotEqual(log["source_url"][0]["status"], 200)
        self.assertEqual(log["failures"], ["s1: source_url status 403 / 503"])
    def test_live_status_retry_uses_harness_ua(self):
        import io, urllib.error
        uas = []
        def fake(req, timeout=None):
            uas.append(req.get_header("User-agent")); raise urllib.error.HTTPError(req.full_url, 403, "x", {}, io.BytesIO(b""))
        with tmpd(self) as d:
            sd = Path(d); (sd / "search_evergreen.jsonl").write_text(json.dumps({"id": "s1", "source_url": "https://a.example/"}) + "\n"); (sd / "scrape_urls.jsonl").write_text("")
            with mock.patch("urllib.request.urlopen", fake), mock.patch("builtins.print"), mock.patch.object(freeze.time, "sleep", lambda t: None): rc = freeze.main(["--live", "--sets", str(sd)])
        self.assertEqual(rc, 1); self.assertEqual(uas, [freeze.UA, "AgentWebIndexBot/1.0"])
    def _html_only(self, html):
        return checks.scrape_check(self.ROW, {"text": "Deep learning is here", "html": html}, CFG["challenge_signatures"], CFG["challenge_html_signatures"])
    def test_html_signature_title_alone_fails(self):
        html = self.PAGE.replace("/_fs-ch-", "/assets-")
        self.assertNotIn("_fs-ch-", html); self.assertEqual(self._html_only(html), (False, "challenge page signature: <title>Client Challenge</title>"))
    def test_html_signature_fs_ch_path_alone_fails(self):
        html = self.PAGE.replace("<title>Client Challenge</title>", "<title>Nature</title>")
        self.assertNotIn("Client Challenge", html); self.assertEqual(self._html_only(html), (False, "challenge page signature: /_fs-ch-"))
    def test_live_freeze_cli_needs_live(self):
        with self.assertRaises(SystemExit): freeze.main([])




class RunTestsTmpGuard(unittest.TestCase):
    GUARD = "\n".join(l for l in (SITE / "run_tests.sh").read_text().splitlines() if l.startswith(("left=", "if [ \"$left\"", "echo \"temp hygiene")))
    def _guard(self, planted):
        with tmpd(self) as d:
            t = Path(d) / "t"; t.mkdir()
            for n in planted: (t / n).mkdir()
            return subprocess.run(["bash", "-c", self.GUARD], env={**os.environ, "TMPDIR": str(t)}, capture_output=True, text=True)
    def test_guard_lines_present(self):
        self.assertEqual(len(self.GUARD.splitlines()), 3)
    def test_planted_awi_bd_dir_fails(self):
        r = self._guard(["awi-bd-dummy"]); self.assertEqual(r.returncode, 1); self.assertIn("left 1 awi-* temp dirs behind", r.stdout)
    def test_planted_awi_test_dir_fails(self):
        r = self._guard(["awi-test-dummy", "awi-dry-dummy"]); self.assertEqual(r.returncode, 1); self.assertIn("left 2 awi-* temp dirs behind", r.stdout)
    def test_clean_or_foreign_dirs_pass(self):
        r = self._guard(["other-dummy"]); self.assertEqual(r.returncode, 0); self.assertIn("temp hygiene OK", r.stdout)



class Rule8Rule9(unittest.TestCase):
    FIX = Path(__file__).parent / "fixtures" / "challenge"
    def _sets(self, d, mutate):
        ev, sc, ts = mocks.make_sets(Path(d)); mutate(sc)
        (Path(d) / "scrape_urls.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sc))
        return validate.validate(d, mocks.mock_cfg(CFG))[0]
    def _same_host(self, n, cls="static"):
        def m(sc):
            for r in [r for r in sc if r["class"] == cls][:n]: r["url"] = "https://www.pub.example/" + r["id"]
        return m
    def test_host_cap_5_per_class_passes(self):
        with tmpd(self) as d: self.assertEqual(self._sets(d, self._same_host(5)), [])
    def test_host_cap_6_per_class_fails(self):
        with tmpd(self) as d: errs = self._sets(d, self._same_host(6))
        self.assertEqual(errs, ["scrape_urls: 6 static rows on host pub.example, max 5 per host per class (rule 8)"])
    def test_host_strip_www_normalizes_to_one_host(self):
        from awibench.util import domain
        self.assertEqual({domain("https://www.x.com/a"), domain("https://x.com/b"), domain("https://WWW.X.COM:443/c")}, {"x.com"})
    def test_host_cap_mixed_www_and_bare_is_one_host(self):
        def m(sc):
            for i, r in enumerate([r for r in sc if r["class"] == "static"][:6]): r["url"] = ("https://www.x.com/" if i % 2 else "https://x.com/") + r["id"]
        with tmpd(self) as d: errs = self._sets(d, m)
        self.assertEqual(len(errs), 1); self.assertIn(" 6 static rows on host x.com,", errs[0])
        def five(sc):
            for i, r in enumerate([r for r in sc if r["class"] == "static"][:5]): r["url"] = ("https://www.x.com/" if i % 2 else "https://x.com/") + r["id"]
        with tmpd(self) as d: self.assertEqual(self._sets(d, five), [])
    def test_freeze_host_delay_is_pinned_at_3s(self):
        self.assertEqual(CFG["freeze_host_delay_s"], 3.0)
        for elapsed, want in ((3.0, []), (2.9, [round(3.0 - 2.9, 6)])):
            now = [0.0]; slept = []
            th = freeze.HostThrottle(CFG["freeze_host_delay_s"], clock=lambda: now[0], sleep=lambda t: slept.append(round(t, 6)))
            th.wait("https://a.example/1"); now[0] += elapsed; th.wait("https://a.example/2")
            self.assertEqual(slept, want, elapsed)
    def test_host_cap_counts_per_class(self):
        def m(sc): self._same_host(5, "static")(sc); self._same_host(5, "anti_bot")(sc)
        with tmpd(self) as d: self.assertEqual(self._sets(d, m), [])
    def test_paywall_lite_min_chars_floor(self):
        def low(sc): next(r for r in sc if r["class"] == "paywall_lite")["min_chars"] = 4999
        def at(sc): next(r for r in sc if r["class"] == "paywall_lite")["min_chars"] = 5000
        def other(sc): next(r for r in sc if r["class"] == "static")["min_chars"] = 100
        with tmpd(self) as d: errs = self._sets(d, low)
        self.assertEqual(len(errs), 1); self.assertIn("paywall_lite min_chars 4999 < 5000 (rule 9)", errs[0])
        with tmpd(self) as d: self.assertEqual(self._sets(d, at), [])
        with tmpd(self) as d: self.assertEqual(self._sets(d, other), [])
    def test_duplicate_marker_fails(self):
        def dup(sc): sc[7]["marker"] = {"type": "text_contains", "value": sc[2]["marker"]["value"].upper() + "."}
        with tmpd(self) as d: errs = self._sets(d, dup)
        self.assertEqual(errs, ["scrape_urls L8: marker duplicates L3 (rule 9: each marker unique to its page)"])
    def test_cloudflare_fixture_is_challenge(self):
        h = (self.FIX / "cloudflare_just_a_moment.html").read_text()
        self.assertEqual(checks.scrape_check(ChallengeInterstitial.ROW, {"text": "Deep learning " + freeze.html_text(h), "html": h}, CFG["challenge_signatures"], CFG["challenge_html_signatures"]), (False, "challenge page signature: Just a moment..."))
    def test_perimeterx_fixture_is_challenge(self):
        h = (self.FIX / "perimeterx_access_denied.html").read_text()
        self.assertEqual(checks.scrape_check(ChallengeInterstitial.ROW, {"text": "Deep learning " + freeze.html_text(h), "html": h}, CFG["challenge_signatures"], CFG["challenge_html_signatures"]), (False, "challenge page signature: Access to this page has been denied"))
    def test_host_throttle_waits_per_host(self):
        now = [100.0]; slept = []
        th = freeze.HostThrottle(3.0, clock=lambda: now[0], sleep=lambda t: (slept.append(t), now.__setitem__(0, now[0] + t)))
        th.wait("https://www.a.example/1"); now[0] += 1.0; th.wait("https://a.example/2"); th.wait("https://b.example/1"); now[0] += 5.0; th.wait("https://a.example/3")
        self.assertEqual(slept, [2.0])
    def test_host_throttle_zero_never_sleeps(self):
        slept = []; th = freeze.HostThrottle(0, clock=lambda: 0.0, sleep=slept.append)
        th.wait("https://a.example/"); th.wait("https://a.example/"); self.assertEqual(slept, [])

class FreezePlainFallback(unittest.TestCase):
    ROW, GOOD, BAD, _live = ChallengeInterstitial.ROW, ChallengeInterstitial.GOOD, ChallengeInterstitial.BAD, ChallengeInterstitial._live
    def ok_plain(self, u, **k): return {"status": 200, "html": "<p>Deep learning works</p>", "text": "Deep learning works"}
    def test_plain_ok_uses_plain_no_browser(self):
        with tmpd(self) as d: rc, log, calls = self._live(d, self.BAD, plain=self.ok_plain)
        self.assertEqual(rc, 0); self.assertEqual(calls, []); self.assertEqual(log["markers"][0]["attempts"], [{"ok": True, "reason": "marker found, length ok", "method": "plain"}])
    def test_plain_403_falls_back_to_reference_browser(self):
        with tmpd(self) as d: rc, log, calls = self._live(d, self.GOOD)
        self.assertEqual(rc, 0); self.assertEqual(len(calls), 1)
        self.assertEqual(log["markers"][0]["attempts"], [{"ok": True, "reason": "marker found, length ok", "method": "reference_browser", "fallback": "plain refused: HTTP 403"}])
    def test_plain_429_falls_back(self):
        with tmpd(self) as d: rc, log, calls = self._live(d, self.GOOD, plain=lambda u, **k: {"status": 429, "html": "", "text": ""})
        self.assertEqual(log["markers"][0]["attempts"][0]["fallback"], "plain refused: HTTP 429"); self.assertEqual(len(calls), 1)
    def test_plain_challenge_page_falls_back(self):
        h = (Rule8Rule9.FIX / "cloudflare_just_a_moment.html").read_text()
        with tmpd(self) as d: rc, log, calls = self._live(d, self.GOOD, plain=lambda u, **k: {"status": 200, "html": h, "text": freeze.html_text(h)})
        a = log["markers"][0]["attempts"][0]; self.assertEqual((a["method"], a["fallback"], a["ok"]), ("reference_browser", "plain refused: challenge page signature: Just a moment...", True))
    MISS = lambda self, u, **k: {"status": 200, "html": "<p>x</p>", "text": "x"}
    def test_plain_200_missing_marker_falls_back_and_browser_has_it(self):
        with tmpd(self) as d: rc, log, calls = self._live(d, self.GOOD, plain=self.MISS)
        self.assertEqual(rc, 0); self.assertEqual(len(calls), 1)
        self.assertEqual(log["markers"][0]["attempts"], [{"ok": True, "reason": "marker found, length ok", "method": "reference_browser", "fallback": "plain 200 lacks the marker (rule 10)"}])
    def test_plain_200_missing_marker_and_browser_lacks_it_fails(self):
        with tmpd(self) as d: rc, log, calls = self._live(d, "print('<html><body><p>nothing here</p></body></html>')", plain=self.MISS)
        m = log["markers"][0]
        self.assertEqual(rc, 1); self.assertFalse(m["ok"]); self.assertEqual(len(calls), 2)
        self.assertEqual([(a["ok"], a["reason"], a["method"], a["fallback"]) for a in m["attempts"]], [(False, "marker not found", "reference_browser", "plain 200 lacks the marker (rule 10)")] * 2)
        self.assertEqual([ua for _, ua in self.plain_calls], [None, "AgentWebIndexBot/1.0"])
    def test_js_heavy_goes_straight_to_reference_browser(self):
        with tmpd(self) as d: rc, log, calls = self._live(d, self.GOOD, plain=self.ok_plain, row=dict(self.ROW, **{"class": "js_heavy"}))
        self.assertEqual(self.plain_calls, []); self.assertEqual(len(calls), 1); self.assertEqual(log["markers"][0]["attempts"][0]["method"], "reference_browser")
    def test_retry_records_method_per_attempt(self):
        with tmpd(self) as d: rc, log, calls = self._live(d, f"if n == 1: {self.BAD}\nelse: {self.GOOD}")
        att = log["markers"][0]["attempts"]
        self.assertEqual(rc, 0); self.assertEqual([(a["ok"], a["method"], a["fallback"]) for a in att], [(False, "reference_browser", "plain refused: HTTP 403"), (True, "reference_browser", "plain refused: HTTP 403")])
        self.assertEqual([ua for _, ua in self.plain_calls], [None, "AgentWebIndexBot/1.0"])
    def test_live_throttle_uses_config_delay_on_same_host(self):
        with tmpd(self) as d: rc, log, calls = self._live(d, self.GOOD)
        self.assertEqual(self.sleeps, [3.0])



class FixtureHygiene(unittest.TestCase):
    DIR = Path(__file__).parent / "fixtures"
    UUID0 = "00000000-0000-0000-0000-000000000000"
    PATTERNS = [(r"__cf_chl_[a-z_]+=([^&\"' ;]+)", "REDACTED-TOKEN"), (r"cRay:\s*'([^']*)'", "REDACTED-RAY"), (r"[?&]ray=([^&\"' ;]+)", "REDACTED-RAY"),
                (r"\bcH:\s*'([^']*)'", "REDACTED-TOKEN"), (r"\bmd:\s*'([^']*)'", "REDACTED-TOKEN"), (r"\bcN:\s*'([^']*)'", "REDACTED-TOKEN"),
                (r"_pxUuid\s*=\s*'([^']*)'", UUID0), (r"[?&]u=([^&\"' ;]+)", UUID0), (r"[?&]b=([^&\"' ;]+)", "REDACTED-TOKEN")]
    UUID = r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
    def bad_values(self, text):
        import re
        bad = [(p, v) for p, want in self.PATTERNS for v in re.findall(p, text) if v != want]
        return bad + [("px uuid", v) for v in re.findall(self.UUID, text) if v != self.UUID0]
    def test_challenge_fixtures_carry_only_placeholders(self):
        files = sorted((self.DIR / "challenge").glob("*.html")); self.assertGreaterEqual(len(files), 3)
        for f in files: self.assertEqual(self.bad_values(f.read_text()), [], f.name)
    def test_hygiene_check_catches_real_tokens(self):
        self.assertEqual(len(self.bad_values("cRay: '" + "0123456789" + "abcdef" + "',cH: 'abc',__cf_chl_tk=Xyz.1-2&u=12345678-1234-1234-1234-123456789abc")), 5)
    def test_signatures_survive_redaction(self):
        for f, sig in (("cloudflare_just_a_moment.html", "Just a moment..."), ("perimeterx_access_denied.html", "Access to this page has been denied")):
            self.assertIn(sig, freeze.html_text((self.DIR / "challenge" / f).read_text()))


if __name__ == "__main__": unittest.main()


class UrlExactList(unittest.TestCase):
    ALLOW = ["https://www.rfc-editor.org/rfc/rfc8259", "https://www.rfc-editor.org/rfc/rfc8259.html",
             "https://www.rfc-editor.org/rfc/rfc8259.txt", "https://datatracker.ietf.org/doc/html/rfc8259"]
    def res(self, *urls): return [{"url": u} for u in urls]
    def test_list_passes_on_any_normalised_member_in_top10(self):
        ch = {"type": "url_exact", "value": self.ALLOW}
        for u in ("https://rfc-editor.org/rfc/rfc8259/", "http://www.rfc-editor.org/rfc/rfc8259.txt", "https://datatracker.ietf.org/doc/html/rfc8259"):
            self.assertEqual(checks.search_check(ch, self.res(*["https://x.example/%d" % i for i in range(9)], u)), (True, "url_exact in top10"), u)
    def test_list_fails_outside_top10_or_no_member(self):
        ch = {"type": "url_exact", "value": self.ALLOW}
        self.assertFalse(checks.search_check(ch, self.res(*["https://x.example/%d" % i for i in range(10)], self.ALLOW[0]))[0])
        self.assertFalse(checks.search_check(ch, self.res("https://www.rfc-editor.org/rfc/rfc8258.html"))[0])
    def test_string_value_unchanged(self):
        ch = {"type": "url_exact", "value": self.ALLOW[1]}
        self.assertTrue(checks.search_check(ch, self.res(self.ALLOW[1]))[0]); self.assertFalse(checks.search_check(ch, self.res(self.ALLOW[0]))[0])
    def test_prefix_match_rejected(self):
        for v in (self.ALLOW[0], self.ALLOW):   # string and list forms
            ch = {"type": "url_exact", "value": v}
            for u in ("https://www.rfc-editor.org/rfc/rfc8259-errata", "https://www.rfc-editor.org/rfc/rfc8259/section-1"):
                self.assertEqual(checks.search_check(ch, self.res(u))[0], False, (v, u))
    def test_substring_match_rejected(self):
        for v in (self.ALLOW[0], self.ALLOW):
            ch = {"type": "url_exact", "value": v}
            for u in ("https://mirror.example/https://www.rfc-editor.org/rfc/rfc8259", "https://mirror.example/r?u=https://www.rfc-editor.org/rfc/rfc8259"):
                self.assertEqual(checks.search_check(ch, self.res(u))[0], False, (v, u))
    def _ev(self, d, value, typ="url_exact"):
        ev, sc, ts = mocks.make_sets(Path(d)); ev[0]["check"] = {"type": typ, "value": value}
        (Path(d) / "search_evergreen.jsonl").write_text("".join(json.dumps(r) + "\n" for r in ev))
        return validate.validate(d, mocks.mock_cfg(CFG))[0]
    def test_validator_accepts_nonempty_url_list(self):
        with tmpd(self) as d: self.assertEqual(self._ev(d, self.ALLOW), [])
    def test_validator_rejects_bad_lists(self):
        for v, t in (([], "url_exact"), (["not a url"], "url_exact"), ([self.ALLOW[0], 3], "url_exact"), (self.ALLOW, "url_domain")):
            with tmpd(self) as d: errs = self._ev(d, v, t)
            self.assertEqual(len(errs), 1, (v, t)); self.assertIn("check.value list must be a non-empty list", errs[0])
    def test_drafts_style_row_validates(self):
        with tmpd(self) as d: self.assertEqual(self._ev(d, self.ALLOW[:1]), [])

class DryRunCleanup(unittest.TestCase):
    def test_dry_run_removes_its_tempdir_before_exit(self):
        import runpy
        with mock.patch("builtins.print"): g = runpy.run_path(str(Path(__file__).parent / "dry_run.py"))
        td = g["td"]; self.addCleanup(td.cleanup)   # td is still referenced, so only the script's own cleanup() can have removed it
        self.assertFalse(Path(td.name).exists(), td.name)

class ApifyMeterGuard(unittest.TestCase):
    PROV = [{"id": "apify", "categories": ["search"], "key_env": "APIFY_API_TOKEN"}]
    PRICING = {"apify": {"search": {"model": "per_request", "free_tier": {"units_per_month": 500, "needs_card": False, "pool": "apify_usd_cents"}}}}
    def setUp(self): self.tmp = mktmp(self); self.called = []
    def _fn(self, key, item): self.called.append(item["id"]); return {"results": [], "billed_units": 1}
    def _run(self, meter, ledger_cents=0, n=5):
        (self.tmp / "q.json").write_text(json.dumps({"month": "2026-10", "used": {"apify_usd_cents": ledger_cents}, "over_free_units": 0}))
        rows = [{"id": f"n{i}", "query": "q", "check": {"type": "domain_in_top5", "value": "x.example"}} for i in range(n)]
        s = runner.run(providers=self.PROV, sets={"search_evergreen": {"rows": rows, "sha": "x"}}, pricing=self.PRICING, tos={}, adapters={"apify": {"search": self._fn}},
                       cfg={"meter_budget": CFG["meter_budget"]}, runs_dir=self.tmp / "runs", ledger_path=self.tmp / "q.json", run_date="2026-10-20",
                       env={"APIFY_API_TOKEN": "mock"}, mock=True, spend_dir=self.tmp / "spend", meters={"apify": meter})
        return s["providers"][0]
    def test_config_meter_budget(self):
        self.assertEqual({k: CFG["meter_budget"]["apify"][k] for k in ("stop_usd", "cap_usd", "ledger_unit_usd")}, {"stop_usd": 4.0, "cap_usd": 5.0, "ledger_unit_usd": 0.01})
    def test_meter_above_ledger_wins_and_trips_at_4(self):
        p = self._run(lambda: 4.0, ledger_cents=184)
        self.assertEqual((p["status"], p["calls"], self.called), ("stale_budget", 0, []))
        self.assertIn("max(meter $4.0000, ledger $1.8400)", p["reason"])
        p = self._run(lambda: 3.995, ledger_cents=184); self.assertEqual((p["status"], self.called), ("stale_budget", []))
        p = self._run(lambda: 3.0, ledger_cents=184); self.assertEqual((p["status"], p["calls"]), ("ran", 5))
    def test_ledger_above_meter_wins(self):
        p = self._run(lambda: 0.5, ledger_cents=399.5); self.assertEqual((p["status"], self.called), ("stale_budget", []))
    def test_meter_unreadable_skips_apify(self):
        def boom(): raise RuntimeError("HTTP 500")
        for m in (boom, lambda: None, lambda: float("nan"), lambda: -1.0):
            self.called = []; p = self._run(m)
            self.assertEqual((p["status"], p["calls"], self.called), ("stale_budget", 0, []))
            self.assertIn("usage meter unreadable; failing closed (rule 6)", p["reason"])
        s = runner.run(providers=self.PROV, sets={"search_evergreen": {"rows": [{"id": "n0", "query": "q", "check": {}}], "sha": "x"}}, pricing=self.PRICING, tos={},
                       adapters={"apify": {"search": self._fn}}, cfg={"meter_budget": CFG["meter_budget"]}, runs_dir=self.tmp / "r2", ledger_path=self.tmp / "q2.json",
                       run_date="2026-10-20", env={"APIFY_API_TOKEN": "mock"}, mock=True, spend_dir=self.tmp / "spend")
        self.assertEqual(s["providers"][0]["status"], "stale_budget")
    def test_meter_and_ledger_and_gap_logged(self):
        reads = iter([1.0] * 5 + [1.5])
        p = self._run(lambda: next(reads), ledger_cents=90)
        want = {"meter_usd": 1.5, "ledger_usd": 0.95, "meter_minus_ledger_usd": 0.55, "run_meter_usd": 0.5, "run_ledger_usd": 0.05, "run_meter_minus_ledger_usd": 0.45}
        self.assertEqual({k: p[k] for k in want}, want)
        log = json.loads((self.tmp / "spend" / "apify-meter-2026-10.json").read_text())
        self.assertEqual(log["runs"], [{"run_date": "2026-10-20"} | want])
    def test_stop_at_exactly_stop_usd_even_without_next_call_term(self):
        mb = CFG["meter_budget"] | {"apify": CFG["meter_budget"]["apify"] | {"ledger_unit_usd": 0.0}}
        (self.tmp / "q.json").write_text(json.dumps({"month": "2026-10", "used": {"apify_usd_cents": 0}, "over_free_units": 0}))
        rows = [{"id": "n0", "query": "q", "check": {"type": "domain_in_top5", "value": "x.example"}}]
        s = runner.run(providers=self.PROV, sets={"search_evergreen": {"rows": rows, "sha": "x"}}, pricing=self.PRICING, tos={}, adapters={"apify": {"search": self._fn}},
                       cfg={"meter_budget": mb}, runs_dir=self.tmp / "runs", ledger_path=self.tmp / "q.json", run_date="2026-10-20",
                       env={"APIFY_API_TOKEN": "mock"}, mock=True, spend_dir=self.tmp / "spend", meters={"apify": lambda: 4.0})
        self.assertEqual((s["providers"][0]["status"], self.called), ("stale_budget", []))

class ApifyMainWiring(unittest.TestCase):
    """main() must hand runner.run the live Apify usage meter (apify_meter with the run's token), not None."""
    def test_main_passes_live_meter_to_guard(self):
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        seen = {}
        class Stop(Exception): pass
        def fake_run(**kw): seen.update(kw); raise Stop()
        env = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_API_TOKEN", "_TOKEN", "_KEY"))} | {"APIFY_API_TOKEN": "mock-dummy"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "argv", ["benchmark.py", "--mode", "smoke", "--only-providers", "apify"]), \
             mock.patch.object(B.runner, "run", fake_run), mock.patch.object(B, "apify_meter", return_value=3.21) as am, \
             mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")), mock.patch("builtins.print"):
            with self.assertRaises(Stop): B.main()
            self.assertIsNotNone(seen.get("meters")); self.assertIn("apify", seen["meters"])
            self.assertEqual(seen["meters"]["apify"](), 3.21)   # read while the run's env is in place
        am.assert_called_once_with("mock-dummy")


class CanonScoring(unittest.TestCase):
    ART = "https://www.bbc.co.uk/news/articles/cq398zl8jmvdo?at_medium=RSS&at_campaign=rss"
    ROW = {"id": "n1", "published_at": "2026-10-16T14:19:50+00:00", "check": {"type": "url_exact", "value": ART},
           "alt_check": {"type": "url_domain", "value": "bbc.co.uk", "within_h": 72}}
    def test_canonical_url_same_host_and_tracking_stripped(self):
        from awibench.util import norm_url, domain
        self.assertEqual(norm_url(self.ART), norm_url("https://www.bbc.com/news/articles/cq398zl8jmvdo"))
        self.assertEqual(norm_url("https://x.example/a?utm_source=rss&UTM_Medium=x&id=7&at_medium=RSS"), "https://x.example/a?id=7")
        self.assertNotEqual(norm_url("https://x.example/a?id=7"), norm_url("https://x.example/a?id=8"))
        self.assertEqual({domain("https://www.bbc.com/x"), domain("https://bbc.co.uk/y")}, {"bbc.co.uk"})
    def test_utm_content_and_utm_term_stripped(self):
        from awibench.util import norm_url
        self.assertEqual(norm_url("https://x.example/a?utm_content=hero&id=7"), "https://x.example/a?id=7")
        self.assertEqual(norm_url("https://x.example/a?id=7&utm_term=ai+search"), "https://x.example/a?id=7")
        self.assertEqual(norm_url("https://x.example/a?UTM_Content=a&utm_term=b"), "https://x.example/a")
    def test_google_redirect_unwrapped_and_srsltid_stripped(self):
        from awibench.util import norm_url, domain
        art = "https://www.bbc.co.uk/news/articles/cq398zl8jmvdo"
        self.assertEqual(norm_url("http://www.google.com/goto?url=" + art.replace(":", "%3A").replace("/", "%2F")), norm_url(art))
        self.assertEqual(norm_url("https://www.google.com/url?q=" + art + "%3Futm_source%3Dx&sa=U"), norm_url(art))
        self.assertEqual(norm_url("http://www.google.com/goto?url=CAESopaqueTOKEN123"), "https://google.com/goto")   # opaque token dropped, not resolvable offline
        self.assertEqual(norm_url("https://x.example/p/1?srsltid=AbC123&id=2"), "https://x.example/p/1?id=2")
        self.assertEqual(checks.news_check(self.ROW, [{"url": "http://www.google.com/goto?url=" + art, "published_at": None}]), (True, "url_exact in top10"))
    def test_no_date_passes_only_on_exact_canonical_article_id(self):
        same = [{"url": "https://www.bbc.com/news/articles/cq398zl8jmvdo", "published_at": None}]
        other = [{"url": "https://www.bbc.com/news/articles/zzzzzzzzzzzz", "published_at": None}]
        self.assertEqual(checks.news_check(self.ROW, same), (True, "url_exact in top10"))
        self.assertEqual(checks.news_check(self.ROW, other), (False, "domain not in top5"))
    def test_dated_same_domain_fallback_unchanged(self):
        dated = [{"url": "https://www.bbc.com/news/articles/zzzzzzzzzzzz", "published_at": "2026-10-17T00:00:00+00:00"}]
        old = [{"url": "https://www.bbc.com/news/articles/zzzzzzzzzzzz", "published_at": "2026-10-01T00:00:00+00:00"}]
        self.assertTrue(checks.news_check(self.ROW, dated)[0]); self.assertFalse(checks.news_check(self.ROW, old)[0])

class DomainShareCap(unittest.TestCase):
    def _rows(self, n_bbc, n=20):
        return [{"id": f"r{i}", "feed": f"f{i % 5}", "check": {"type": "url_exact", "value": "x"},
                 "source_url": ("https://www.bbc.com/a%d" % i if i % 2 else "https://bbc.co.uk/a%d" % i) if i < n_bbc else "https://o%d.example/a" % i} for i in range(n)]
    def _errs(self, rows, name):
        e, w = [], []; validate.check_news(rows, CFG, e, w, name); return [x for x in e if "cap (from week" in x]
    def test_cap_is_config_25pct_from_week_44(self):
        self.assertEqual((CFG["news"]["max_domain_share"], CFG["news"]["domain_share_from_week"]), (0.25, "2026-44"))
    def test_over_25pct_fails_from_week_44_with_aliases_counted_as_one(self):
        self.assertEqual(self._errs(self._rows(5), "news_2026-44.jsonl"), [])
        errs = self._errs(self._rows(6), "news_2026-44.jsonl")
        self.assertEqual(errs, ["news_2026-44.jsonl: source domain bbc.co.uk has 6/20 rows > 25% cap (from week 2026-44)"])
        self.assertEqual(len(self._errs(self._rows(6), "news_2027-01.jsonl")), 1)
    def test_boundary_25_of_100_passes_26_of_100_fails(self):
        self.assertEqual(self._errs(self._rows(25, n=100), "news_2026-44.jsonl"), [])
        self.assertEqual(self._errs(self._rows(26, n=100), "news_2026-44.jsonl"),
                         ["news_2026-44.jsonl: source domain bbc.co.uk has 26/100 rows > 25% cap (from week 2026-44)"])
    def test_weeks_before_44_not_capped(self):
        self.assertEqual(self._errs(self._rows(20), "news_2026-43.jsonl"), [])


class RegradeFromStoredResults(unittest.TestCase):
    FIX = Path(__file__).parent / "fixtures" / "rescore"
    def _runs(self):
        d = mktmp(self) / "runs"
        shutil.copytree(self.FIX / "runs", d)
        for f in (BENCH / "runs").glob("*/*/news_2026-42.jsonl"):
            t = d / f.parent.parent.name / f.parent.name; t.mkdir(parents=True, exist_ok=True); shutil.copy(f, t)
        return d
    def _build(self, d):
        doc = tables.build(d, {}, BENCH / "sets", {}, CFG); doc.pop("generated_at"); return doc
    def test_regrade_twice_is_identical_and_logs_stay_raw(self):
        d = self._runs(); before = {f: f.read_bytes() for f in d.rglob("*.jsonl")}
        a, b = self._build(d), self._build(d)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual({f: f.read_bytes() for f in d.rglob("*.jsonl")}, before)
        self.assertEqual(a["grader_version"], checks.GRADER_VERSION)
        self.assertEqual({r["grader_version"] for r in a["tables"]["search"]}, {checks.GRADER_VERSION})
    def test_rescore_numbers_match_rescore_json(self):
        want = {(r["provider"], "news_2026-" + r["week"]): (r["new_pass"], r["n"]) for r in json.loads((self.FIX / "rescore.json").read_text())["rows"] if r["provider"] != "firecrawl"}
        got = {(r["provider"], r["set"]): (r["passed"]["v"], r["attempted"]["v"]) for r in self._build(self._runs())["tables"]["search"]}
        self.assertEqual(got, want)
        self.assertEqual(want[("tavily", "news_2026-43")], (29, 40)); self.assertEqual(want[("tavily", "news_2026-42")], (12, 16))
    def test_firecrawl_week42_rescore_5_to_8_from_stored_log(self):
        rows = {r["id"]: r for r in read_jsonl(BENCH / "sets" / "news_2026-42.jsonl")}
        L = [l for l in read_jsonl(BENCH / "runs" / "2026-10-15" / "firecrawl" / "news_2026-42.jsonl") if l.get("offset_h", 0) == 0 and l["status"] in ("pass", "fail")]
        new = sum(checks.news_check(rows[l["item_id"]], l.get("results", []))[0] for l in L)
        want = [r for r in json.loads((self.FIX / "rescore.json").read_text())["rows"] if r["provider"] == "firecrawl"][0]
        self.assertEqual((len(L), sum(l["pass"] for l in L), new), (want["n"], want["old_pass"], want["new_pass"]))
        self.assertEqual((want["old_pass"], want["new_pass"]), (5, 8))
    def test_non_search_rows_keep_stored_grade(self):
        self.assertEqual(tables.regrade("newsfetch_2026-43", [(1, {"status": "fail"})], BENCH / "sets"), ([(1, {"status": "fail"})], None))
    def test_error_lines_are_not_regraded(self):
        e = [(1, {"status": "fail", "item_id": "news-2026-W43-001", "results": [], "reason": "RuntimeError: HTTP 500: x"})]
        self.assertEqual(tables.regrade("news_2026-43", e, BENCH / "sets")[0], e)


class NewsgenDomainCap(unittest.TestCase):
    def _feed(self, fid, host, n):
        items = "".join(f"<item><title>Story {fid} {i} about events</title><link>https://{host}/news/articles/{fid}{i}?at_medium=RSS</link>"
                        f"<pubDate>Sun, 25 Oct 2026 0{i % 10}:00:00 GMT</pubDate></item>" for i in range(n))
        return f'<?xml version="1.0"?><rss version="2.0"><channel><title>{fid}</title>{items}</channel></rss>'
    def test_bbc_heavy_week44_stays_at_or_under_25pct_and_validates(self):
        feeds = [{"id": f"bbc{k}", "feed_format": "rss", "name": "b", "url": f"https://feeds.example/bbc{k}", "section": "s", "url_verified": True} for k in range(4)] + \
                [{"id": f"o{k}", "feed_format": "rss", "name": "o", "url": f"https://feeds.example/o{k}", "section": "s", "url_verified": True} for k in range(4)]
        hosts = {"bbc0": "www.bbc.co.uk", "bbc1": "www.bbc.com", "bbc2": "www.bbc.co.uk", "bbc3": "www.bbc.com", "o0": "a.example", "o1": "b.example", "o2": "c.example", "o3": "d.example"}
        cfg = dict(CFG, news_feeds=feeds, news=dict(CFG["news"]))
        txt = {f["id"]: self._feed(f["id"], hosts[f["id"]], 15 if f["id"].startswith("bbc") else 4) for f in feeds}
        log = []; run_at = dt.datetime.fromisoformat("2026-10-27T06:00:00+00:00")
        rows, name = newsgen.generate(cfg, run_at, "0", lambda f: txt.get(f["id"]) if not f.get("page") else None, log)
        self.assertEqual(name, "news_2026-44.jsonl"); self.assertTrue(rows)
        from collections import Counter
        c = Counter(r["outlet"] for r in rows); self.assertLessEqual(max(c.values()), 0.25 * len(rows), c)
        e, w = [], []; validate.check_news(rows, cfg, e, w, name); self.assertEqual(e, [])
        old = dict(cfg, news=dict(cfg["news"], domain_share_from_week="2099-01"))
        rows2, _ = newsgen.generate(old, run_at, "0", lambda f: txt.get(f["id"]) if not f.get("page") else None, [])
        self.assertGreater(max(Counter(r["outlet"] for r in rows2).values()), 0.25 * len(rows2))
    def _gen100(self, n_bbc_feeds):
        feeds = [{"id": f"bbc{k}", "feed_format": "rss", "name": "b", "url": f"https://feeds.example/bbc{k}", "section": "s", "url_verified": True} for k in range(n_bbc_feeds)] + \
                [{"id": f"o{k}", "feed_format": "rss", "name": "o", "url": f"https://feeds.example/o{k}", "section": "s", "url_verified": True} for k in range(8)]
        host = lambda fid: ("www.bbc.co.uk" if int(fid[3:]) % 2 else "www.bbc.com") if fid.startswith("bbc") else f"{fid}.example"
        cfg = dict(CFG, news_feeds=feeds, news=dict(CFG["news"], total=100))
        txt = {f["id"]: self._feed(f["id"], host(f["id"]), 15) for f in feeds}
        run_at = dt.datetime.fromisoformat("2026-10-27T06:00:00+00:00")
        rows, name = newsgen.generate(cfg, run_at, "0", lambda f: txt.get(f["id"]) if not f.get("page") else None, [])
        return cfg, rows, name
    def test_boundary_total_100_caps_bbc_at_exactly_25_not_26(self):
        from collections import Counter
        cfg, rows, name = self._gen100(4)
        self.assertEqual(len(rows), 100)
        self.assertEqual(Counter(r["outlet"] for r in rows)["bbc.co.uk"], 25)
        e, w = [], []; validate.check_news(rows, cfg, e, w, name); self.assertEqual([x for x in e if "cap (from week" in x], [])


class ScopedRunMergesLatest(unittest.TestCase):
    """AWI-EVERGREEN-RUN-001 regression: an --only-sets / --only-providers run must merge into latest.json, not overwrite
    earlier skips and not_run_yet entries for providers/categories and sets it did not touch."""
    PREV = {"tables": {"search": [], "scrape": [], "browser": []}, "freshness": [],
            "skips": [{"scope": "set", "set": "search_evergreen", "status": "skipped_invalid_set", "reason": "missing search_evergreen.jsonl"},
                      {"scope": "set", "set": "browser_tasks", "status": "skipped_invalid_set", "reason": "missing browser_tasks.yaml"},
                      {"scope": "provider", "provider": "exa", "category": "scrape", "status": "not_implemented", "reason": "not_implemented"},
                      {"scope": "provider", "provider": "firecrawl", "category": "scrape", "status": "skipped_no_key", "reason": "not run (no key)"},
                      {"scope": "provider", "provider": "tavily", "category": "search", "status": "skipped_no_key", "reason": "not run (no key)"}],
            "not_run_yet": [{"provider": "brightdata", "categories": ["scrape"], "status": "not run yet", "reason": "collector ready; not run yet"}]}

    def _merge(self, s, skips, sets_seen, rows=()):
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        with tmpd(self) as d:
            latest = Path(d) / "latest.json"; latest.write_text(json.dumps(self.PREV))
            doc = {"tables": {"search": [{"provider": p} for p in rows], "scrape": [], "browser": []}, "skips": skips, "not_run_yet": []}
            B.merge_previous(doc, s, latest, sets_seen); return doc

    def test_untouched_skips_and_not_run_yet_survive(self):
        s = {"providers": [{"provider": "exa", "category": "search", "status": "ran"}, {"provider": "exa", "category": "scrape", "status": "not_implemented"},
                           {"provider": "tavily", "category": "search", "status": "ran"}]}
        doc = self._merge(s, [{"scope": "provider", "provider": "exa", "category": "scrape", "status": "not_implemented", "reason": "not_implemented"}],
                          {"search_evergreen"}, rows=("exa", "tavily"))
        keys = [(k.get("scope"), k.get("set") or k.get("provider"), k.get("category"), k["status"]) for k in doc["skips"]]
        self.assertIn(("provider", "firecrawl", "scrape", "skipped_no_key"), keys)        # outside this run: kept
        self.assertIn(("set", "browser_tasks", None, "skipped_invalid_set"), keys)         # set not in this run: kept
        self.assertNotIn(("set", "search_evergreen", None, "skipped_invalid_set"), keys)   # set ran this run: superseded
        self.assertNotIn(("provider", "tavily", "search", "skipped_no_key"), keys)         # provider ran this run: superseded
        self.assertEqual(sum(1 for k in keys if k[1] == "exa" and k[2] == "scrape"), 1)    # no duplicates
        self.assertEqual([n["provider"] for n in doc["not_run_yet"]], ["brightdata"])

    def test_provider_that_ran_leaves_not_run_yet(self):
        s = {"providers": [{"provider": "brightdata", "category": "scrape", "status": "ran"}]}
        doc = self._merge(s, [], set(), rows=())
        self.assertEqual(doc["not_run_yet"], [])

    def test_main_calls_merge_before_publish(self):
        src = (SITE / "collectors" / "benchmark.py").read_text()
        self.assertIn("merge_previous(doc, s, out,", src)
        self.assertLess(src.index("merge_previous(doc, s, out,"), src.index("out.write_text(json.dumps(doc"))

class TavilyPhase0(unittest.TestCase):
    """AWI-PAGE-REDO-001 Phase 0 (R3/R4): Tavily re-verified 2026-10-24 (pricing page + credit-count docs).
    pricing.yaml carries the verified price and today's date on every Tavily input, and latest.json's
    published Tavily rows equal what cost.py/tables.py compute from that pricing.yaml."""
    VERIFIED_USD_PER_CREDIT = 0.008; VERIFIED_DATE = "2026-10-24"
    def test_pricing_yaml_tavily_matches_verified(self):
        t = load_yaml(BENCH / "pricing.yaml")["providers"]["tavily"]["search"]
        self.assertEqual(t["tiers"]["entry"]["name"], "Pay As You Go")
        self.assertEqual(t["tiers"]["entry"]["usd_per_credit"], self.VERIFIED_USD_PER_CREDIT)
        self.assertEqual((t["checked_at"], t["verified_date"]), (self.VERIFIED_DATE, self.VERIFIED_DATE))
        self.assertEqual(t["source_url"], "https://www.tavily.com/pricing")
        ver = t["credits_per_call_verified"]
        self.assertEqual((ver["checked_at"], ver["source_url"]), (self.VERIFIED_DATE, "https://docs.tavily.com/documentation/api-credits"))
    def test_latest_json_tavily_rows_match_pricing_yaml(self):
        doc = json.loads((SITE / "data" / "benchmarks" / "latest.json").read_text())
        built = tables.build(BENCH / "runs", tables.load_pricing(BENCH / "pricing.yaml"), BENCH / "sets",
                             load_yaml(BENCH / "tos_matrix.yaml")["providers"], load_yaml(BENCH / "config.yaml"))
        pub = [r for rows in doc["tables"].values() for r in rows if r["provider"] == "tavily"]
        exp = [r for rows in built["tables"].values() for r in rows if r["provider"] == "tavily"]
        self.assertTrue(pub); self.assertEqual(pub, exp)
        for r in pub: self.assertIn(f"(checked {self.VERIFIED_DATE})", r["cost_units_note"])


# R23 (AC27b-d): an X reply from a verified vendor officer is a valid vendor record; the R19 validator is unchanged. FIXTURE ONLY (example text, not the vendor's words).
X_REPLY_CONFIRMATION = {"date": "2026-10-26", "sender": "@example_vendor_ceo (Example Person, CEO)", "quote": "Example text: yes, the free tier may be used for this benchmark.",
                        "evidence_path": "evidence/vendor/example/x-reply-0000000000000000001.html", "recorded_pr": "https://github.com/hazeydata/agentwebindex-meta/pull/999"}
MOCK_CONFIRMATION = {"date": "2026-11-01", "sender": "Mock Sender, mock@example.invalid", "quote": "mock written permission", "evidence_path": "mock/evidence.md", "recorded_pr": "https://github.com/hazeydata/agentwebindex/pull/999"}

class ContextDevBase(unittest.TestCase):
    """AWI-PROVIDER-CONTEXTDEV-001 spec rev 4 (MOCK ONLY; urlopen is always patched or never reached). Tests are labelled with AC numbers.
    self.cfg is the committed config with the R9 live gate opened and grant_anchor_day 15, so the guard logic is reachable; the committed gate is tested separately."""
    TS = "2026-10-26T10:00:00+00:00"   # anchor 15 -> window 2026-10-15
    W = "2026-10-15"
    def setUp(self):
        self.tmp = mktmp(self); self.cfg = mocks.mock_cfg(CFG)
        self.cfg["free_tier_guard"]["contextdev"].update(live_run_allowed=True, vendor_confirmation=dict(MOCK_CONFIRMATION), grant_anchor_day=15)
        self.pricing = load_yaml(BENCH / "pricing.yaml")["providers"]; self.tos = load_yaml(BENCH / "tos_matrix.yaml")["providers"]
        self.tos["contextdev"] = self.tos["contextdev"] | {"vendor_confirmation": MOCK_CONFIRMATION}   # in-memory R10 record so the live/publish paths are reachable
        sd = self.tmp / "sets"; mocks.make_sets(sd); self.rows = read_jsonl(sd / "search_evergreen.jsonl"); self.srows = read_jsonl(sd / "scrape_urls.jsonl")
        self.spend = self.tmp / "spend"; self.spend.mkdir(); self.calls = []; self.sleeps = []
    def prov_def(self, kinds=("search",)): return [{"id": "contextdev", "categories": list(kinds), "key_env": "CONTEXT_DEV_API_KEY"}]
    def seed(self, attempted, billed=0, window=None):
        w = window or self.W
        (self.spend / f"contextdev-{w}.json").write_text(json.dumps({"provider": "contextdev", "window": w, "attempted": attempted, "billed": billed}))
    def counter(self, window=None): return json.loads((self.spend / f"contextdev-{window or self.W}.json").read_text())
    def ad_search(self, key, item): self.calls.append(item["id"]); return {"http_status": 200, "billed_units": 1, "results": [{"url": "https://a.example/", "title": "t", "snippet": "s"}]}
    def ad_scrape(self, key, item): self.calls.append(item["id"]); return {"http_status": 200, "billed_units": 1, "text": "x", "html": "<p>x</p>"}
    adapter = ad_search
    def go(self, n=3, kinds=("search",), adapters=None, cfg=None, pricing=None, meters=None, providers=None, tos=None, env=None, ts=None, run_date="2026-10-26",
           ledger="ledger.json", sets=None):
        ts = ts or self.TS
        sets = sets or ({"search_evergreen": {"rows": self.rows[:n], "sha": "x"}} if "search" in kinds else {}) | ({"scrape_urls": {"rows": self.srows[:n], "sha": "x"}} if "scrape" in kinds else {})
        ad = adapters or {"contextdev": {"search": self.ad_search, "scrape": self.ad_scrape}}
        return runner.run(providers=providers or self.prov_def(kinds), sets=sets, pricing=pricing or self.pricing, tos=tos if tos is not None else self.tos, adapters=ad,
                          cfg=cfg or self.cfg, runs_dir=self.tmp / "runs", ledger_path=self.tmp / ledger, run_date=run_date, env=env or {"CONTEXT_DEV_API_KEY": "mock-key"},
                          mock=True, spend_dir=self.spend, meters=meters, now_fn=lambda: dt.datetime.fromisoformat(ts), sleep=self.sleeps.append)
    def prov(self, s, pid="contextdev", cat=None): return next(p for p in s["providers"] if p["provider"] == pid and (cat is None or p["category"] == cat))
    def lines(self, kind="search"): return read_jsonl(self.tmp / f"runs/2026-10-26/contextdev/{'search_evergreen' if kind == 'search' else 'scrape_urls'}.jsonl")
    def cfg_with(self, **g):
        c = json.loads(json.dumps(self.cfg)); c["free_tier_guard"]["contextdev"].update(g); return c

def http_err(code, text="vendor text"):
    def f(key, item): raise RuntimeError(f"HTTP {code}: {text}")
    return f

class ContextDevConfig(ContextDevBase):
    def test_ac2_no_price_in_pricing_and_cost_na(self):   # AC2
        for kind in ("search", "scrape"):
            e = self.pricing["contextdev"][kind]
            for tier in e["tiers"].values():
                for k, v in tier.items(): self.assertEqual(v, "NOT CAPTURED", (kind, k))   # no numeric price field
            self.assertNotRegex(json.dumps(e), r"TBD|PLACEHOLDER|XXX")
            for q in ("$20", "$19", "7,500", "$3.00", "$2.20"): self.assertIn(q, e["note"])   # the conflict is quoted, not resolved
            self.assertEqual(cost.cost_columns(e, [{"status": "pass", "billed_units": 1}] * 3)["entry_tier_cost_per_1k"], None)
            self.assertEqual(cost.cost_columns(e, [{"status": "pass", "billed_units": 1}] * 3)["volume_tier_cost_per_1k"], None)
            self.assertEqual((e["credits_per_call"], e["free_tier"]["units_per_month"], e["free_tier"]["needs_card"], e["free_tier"]["pool"], e["free_tier"]["refill"]),
                             ({"search": 1, "scrape": 1}, 1000, False, "contextdev", "grant-anchor day"))
            self.assertEqual(e["credits_per_call_rule"]["source_url"], "https://docs.context.dev/account/credits")
            self.assertEqual(e["credits_per_call_rule"]["scrape"], "1; 2 with browser actions")
    def test_ac3_p0_still_not_passed(self):   # AC3: the committed state keeps P0 unpassed (recorded as still broken in summary.md, meta repo)
        for kind in ("search", "scrape"): self.assertIsNone(cost.billed_usd(self.pricing["contextdev"][kind], "entry", []))
        self.assertFalse(tables.publish_gate("contextdev", self.pricing, self.tos)[0]); self.assertIn("P0 pricing not passed", tables.publish_gate("contextdev", self.pricing, self.tos)[1])
    def test_ac4_tos_entry_matches_rev4_shape(self):   # AC4
        t = self.tos["contextdev"]
        self.assertEqual((t["terms_url"], t["sections"], t["verdict"], t["allowed"], t["full_read"], t["terms_last_updated"]), ("https://www.context.dev/term", 27, "unverified", None, True, "2026-10-01"))
        for c in ("benchmarking", "publishing_results", "automated_access", "attribution", "anti_bot_scraping"): self.assertTrue(t[c]["quote"].strip(), c)
        self.assertIn("23. User data", t["benchmarking"]["quote"]); self.assertIn("Restrictions on Use", t["publishing_results"]["quote"])
        self.assertIn("§4", t["automated_access"]["quote"]); self.assertIn("§8", t["automated_access"]["quote"]); self.assertEqual(t["automated_access"]["status"], "forbidden_literal")
        self.assertIn("§3", t["attribution"]["quote"]); self.assertIn("/brand", t["attribution"]["quote"])
        self.assertEqual((t["benchmarking"]["status"], t["publishing_results"]["status"]), ("unclear", "unclear"))
    def test_ac6_name_plain_text_and_no_logo(self):   # AC6
        provs = json.loads((SITE / "collectors/providers.json").read_text())["providers"]
        self.assertEqual(next(p for p in provs if p["id"] == "contextdev")["name"], "Context.dev")
        skip = {".git", "__pycache__", "node_modules"}; imgs = (".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".gif")
        url = re.compile(r"context\.dev[^\s\"']*(logo|\.(svg|png|jpe?g|webp|ico))", re.I)
        for f in SITE.rglob("*"):
            if not f.is_file() or skip & set(f.relative_to(SITE).parts): continue
            if f.suffix.lower() in imgs: self.assertNotIn("context", f.name.lower(), str(f))
            elif f.suffix in (".py", ".json", ".yaml", ".md", ".html", ".js", ".css", ".txt", ".yml") and f.name != "test_bench.py":
                self.assertIsNone(url.search(f.read_text(errors="replace")), str(f))
    def test_ac7_providers_json_entry_and_keyed(self):   # AC7
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        provs = json.loads((SITE / "collectors/providers.json").read_text())["providers"]
        self.assertEqual(next(p for p in provs if p["id"] == "contextdev"), {"id": "contextdev", "name": "Context.dev", "categories": ["search", "scrape"],
                         "pricing_url": "https://www.context.dev/pricing", "key_env": "CONTEXT_DEV_API_KEY", "exclude_from_also_priced": True})
        self.assertNotIn("contextdev", B.keyed_providers(provs, {})); self.assertNotIn("contextdev", B.keyed_providers(provs, {"CONTEXT_DEV_API_KEY": ""}))
        self.assertIn("contextdev", B.keyed_providers(provs, {"CONTEXT_DEV_API_KEY": "mock"}))
        self.assertEqual(set(B.ADAPTERS["contextdev"]), {"search", "scrape"})
        self.assertIn("exclude_from_also_priced", json.loads((SITE / "collectors/providers.json").read_text())["_note"])   # new optional field documented (AC28)
    def test_ac8_no_key_skipped_and_others_run(self):   # AC8
        other = {"id": "mockp", "categories": ["search"], "key_env": "K"}
        pricing = self.pricing | {"mockp": {"search": mocks.pricing_fixture("credits")}}
        for env in ({"K": "x", "CONTEXT_DEV_API_KEY": ""}, {"K": "x"}):
            s = self.go(providers=self.prov_def() + [other], adapters={"contextdev": {"search": self.ad_search}} | mocks.MockAdapters().table("mockp", ["search"]), pricing=pricing, env=env)
            self.assertEqual((self.prov(s)["status"], self.calls), ("skipped_no_key", [])); self.assertEqual(self.prov(s, "mockp")["status"], "ran")
            self.assertFalse(list(self.spend.glob("contextdev-*.json")))
    def test_config_block_matches_rev4_shape(self):   # AC10/AC11/AC13/AC6a/AC26 config values, committed file
        g = CFG["free_tier_guard"]["contextdev"]
        self.assertEqual({k: g[k] for k in ("pool", "quota_credits", "stop_at_credits", "grant_anchor_day", "live_run_allowed", "vendor_confirmation", "meter", "max_concurrent",
                                            "count", "retry_after_429_s", "quota_stop_errors", "paid_tier")},
                         {"pool": "contextdev", "quota_credits": 1000, "stop_at_credits": 900, "grant_anchor_day": None, "live_run_allowed": False, "vendor_confirmation": None,
                          "meter": "https://api.context.dev/v1/org/usage", "max_concurrent": 1, "count": "attempts", "retry_after_429_s": 1,
                          "quota_stop_errors": ["HTTP 402 USAGE_EXCEEDED", "HTTP 401 USAGE_EXCEEDED"], "paid_tier": False})
        self.assertIn("<window-start YYYY-MM-DD>", g["counter_file"]); self.assertIn("contextdev", CFG["month1_free_tier_providers"]); self.assertEqual(CFG["publish_hold"], ["contextdev"])
        self.assertNotIn("contextdev", CFG["ceo_free_tier_allowlist"])   # needs_card is false in pricing.yaml; no allowlist exception
    def test_also_priced_helper_eleven_with_flag(self):   # AC22 support (the site repo tests the page itself): helper honours exclude_from_also_priced
        provs = json.loads((SITE / "collectors/providers.json").read_text())["providers"]
        latest = json.loads((SITE / "data/benchmarks/latest.json").read_text())
        names = tables.also_priced_providers(self.pricing, latest, provs)
        self.assertEqual(sorted(names), sorted(["Brave Search API", "Linkup", "Parallel", "Serper", "Firecrawl", "Jina Reader", "Bright Data Web Unlocker",
                                                "Hyperbrowser", "ScrapingBee", "Browserbase", "Steel"]))
        self.assertEqual(len(names), 11); self.assertNotIn("Context.dev", names)
        unflagged = [{k: v for k, v in p.items() if k != "exclude_from_also_priced"} for p in provs]
        self.assertEqual(len(tables.also_priced_providers(self.pricing, latest, unflagged)), 12)   # the flag, not chance, excludes it

class ContextDevLiveGate(ContextDevBase):
    def test_ac6a_live_allowed_function(self):   # AC6a
        real_tos = load_yaml(BENCH / "tos_matrix.yaml")["providers"]; tos = {"contextdev": real_tos["contextdev"] | {"vendor_confirmation": MOCK_CONFIRMATION}}
        self.assertFalse(guard_mod.live_allowed(CFG, real_tos)); self.assertFalse(guard_mod.live_allowed(CFG, tos))   # committed config / committed tos
        g = lambda **kw: {"free_tier_guard": {"contextdev": kw}}
        self.assertFalse(guard_mod.live_allowed(g(live_run_allowed=True, vendor_confirmation=None), tos))
        self.assertFalse(guard_mod.live_allowed(g(live_run_allowed=False, vendor_confirmation=MOCK_CONFIRMATION), tos))
        self.assertFalse(guard_mod.live_allowed(g(live_run_allowed="yes", vendor_confirmation=MOCK_CONFIRMATION), tos))
        self.assertTrue(guard_mod.live_allowed(g(live_run_allowed=True, vendor_confirmation=MOCK_CONFIRMATION), tos))
        # R10: config flags alone are not enough; the tos_matrix record must pass vendor_confirmed
        self.assertEqual(guard_mod.live_allowed(g(live_run_allowed=True, vendor_confirmation=MOCK_CONFIRMATION), real_tos), real_tos["contextdev"].get("vendor_confirmation") is None)   # R22: a null tos record is fine; a set one must be valid
        self.assertFalse(guard_mod.live_allowed(g(live_run_allowed=True, vendor_confirmation=MOCK_CONFIRMATION), {"contextdev": tos["contextdev"] | {"vendor_confirmation": {"date": "2026-11-01"}}}))
        self.assertFalse(guard_mod.live_allowed(g(live_run_allowed=True, vendor_confirmation=MOCK_CONFIRMATION), {}))
    def test_r21_guard_config_record_uses_validator(self):   # R21 (1): guard's live gate runs the config record through gates.record_error, no truthiness
        from awibench import gates
        tos = {"contextdev": load_yaml(BENCH / "tos_matrix.yaml")["providers"]["contextdev"] | {"vendor_confirmation": MOCK_CONFIRMATION}}
        g = lambda vc: {"free_tier_guard": {"contextdev": {"live_run_allowed": True, "vendor_confirmation": vc}}}
        self.assertTrue(guard_mod.live_allowed(g(dict(MOCK_CONFIRMATION)), tos))                                               # positive control
        bad = ["yes", {}, {"date": "2026-11-01"}, "https://example.invalid/reply", dict(MOCK_CONFIRMATION, recorded_pr=27), dict(MOCK_CONFIRMATION, extra=1), ["x"], 1, True]
        for vc in bad:
            self.assertIsNotNone(gates.record_error(vc), vc)                                                                # invalid (all truthy except {}, which R21 lists)
            self.assertFalse(guard_mod.live_allowed(g(vc), tos), vc)
        for vc in ("yes", {}, {"date": "2026-11-01", "sender": "s"}):                                                       # zero live calls through the runner
            s = self.go(cfg=self.cfg_with(vendor_confirmation=vc))
            self.assertEqual((self.prov(s)["status"], self.calls), ("skipped_no_vendor_confirmation", []), vc)
    def test_r22_live_allowed_all_conditions(self):   # R22 (supersedes R21 (1)): every condition must hold, else fail closed (mock)
        base = load_yaml(BENCH / "tos_matrix.yaml")["providers"]["contextdev"]
        ok_cfg = {"free_tier_guard": {"contextdev": {"live_run_allowed": True, "vendor_confirmation": dict(MOCK_CONFIRMATION)}}}
        tos = lambda **kw: {"contextdev": {k: v for k, v in (base | kw).items() if v is not ...}}
        la = guard_mod.live_allowed; F = "vendor_confirmation_required"
        self.assertTrue(la(ok_cfg, tos(**{F: True, "vendor_confirmation": dict(MOCK_CONFIRMATION)})))                        # everything valid
        self.assertTrue(la(ok_cfg, tos(**{F: True, "vendor_confirmation": None})))                                           # tos record not set: only the config record counts
        self.assertFalse(la(ok_cfg, tos(**{F: ..., "vendor_confirmation": dict(MOCK_CONFIRMATION)})))                        # tos flag missing
        self.assertFalse(la(ok_cfg, tos(**{F: False, "vendor_confirmation": dict(MOCK_CONFIRMATION)})))                      # tos flag false
        for flag in ("true", 1, None): self.assertFalse(la(ok_cfg, tos(**{F: flag})), flag)                                    # flag must be bool True
        self.assertFalse(la(ok_cfg, {})); self.assertFalse(la(ok_cfg, None)); self.assertFalse(la(ok_cfg, {"contextdev": None}))   # tos entry missing
        self.assertFalse(la(ok_cfg, {"contextdev": "x"}))
        for bad in ("yes", {}, {"date": "2026-11-01"}, dict(MOCK_CONFIRMATION, recorded_pr=27), None):                      # flag true, invalid config record
            cfg = {"free_tier_guard": {"contextdev": {"live_run_allowed": True, "vendor_confirmation": bad}}}
            self.assertFalse(la(cfg, tos(**{F: True, "vendor_confirmation": dict(MOCK_CONFIRMATION)})), bad)
        for bad in ("yes", {"date": "2026-11-01"}, dict(MOCK_CONFIRMATION, date="2026-02-30"), {}):                         # tos record set but invalid
            self.assertFalse(la(ok_cfg, tos(**{F: True, "vendor_confirmation": bad})), bad)
        for lra in (False, "true", 1, None):                                                                                 # live_run_allowed must be bool True
            cfg = {"free_tier_guard": {"contextdev": {"live_run_allowed": lra, "vendor_confirmation": dict(MOCK_CONFIRMATION)}}}
            self.assertFalse(la(cfg, tos(**{F: True})), lra)
    def test_r23_x_reply_record_valid(self):   # R23 (AC27b-d): X-reply-shaped record passes gates.record_error and live_allowed (mocked); both quote and evidence_path empty fails
        rec = dict(X_REPLY_CONFIRMATION)
        self.assertIsNone(gates_mod.record_error(rec))
        tos = {"contextdev": dict(self.tos["contextdev"], vendor_confirmation_required=True, vendor_confirmation=dict(rec))}
        cfg = self.cfg_with(live_run_allowed=True, vendor_confirmation=dict(rec))
        self.assertTrue(guard_mod.live_allowed(cfg, tos))
        search = mock.Mock(side_effect=self.ad_search)
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network call")) as net:
            s = self.go(n=2, cfg=cfg, tos=tos, adapters={"contextdev": {"search": search}})
        self.assertEqual((self.prov(s)["status"], search.call_count), ("ran", 2)); net.assert_not_called()   # mocked adapter only
        # R26 (supersedes R19's at-least-one; resolves AC27d): BOTH quote and evidence_path must be non-empty strings
        for q, ev in (("", rec["evidence_path"]), (None, rec["evidence_path"]), ("  ", rec["evidence_path"]), (rec["quote"], ""), (rec["quote"], None), (rec["quote"], " "),
                      ("", ""), ("", None), (None, ""), (None, None), ("  ", None), (1, rec["evidence_path"]), (rec["quote"], ["x"])):
            bad = dict(rec, quote=q, evidence_path=ev)
            self.assertIsNotNone(gates_mod.record_error(bad), (q, ev))
            self.assertFalse(guard_mod.live_allowed(self.cfg_with(live_run_allowed=True, vendor_confirmation=bad), tos), (q, ev))
            self.assertFalse(guard_mod.live_allowed(cfg, {"contextdev": dict(tos["contextdev"], vendor_confirmation=bad)}), (q, ev))
    def test_r26_c07_missing_key_or_block_zero_calls(self):   # R26 (1) / C07: live_allowed is consulted unconditionally for contextdev
        valid_tos = {"contextdev": self.tos["contextdev"] | {"vendor_confirmation": dict(MOCK_CONFIRMATION)}}
        no_key = json.loads(json.dumps(self.cfg)); g = no_key["free_tier_guard"]["contextdev"]; del g["live_run_allowed"]; g["vendor_confirmation"] = None
        no_block = json.loads(json.dumps(self.cfg)); del no_block["free_tier_guard"]["contextdev"]
        no_ftg = json.loads(json.dumps(self.cfg)); no_ftg.pop("free_tier_guard")
        key_only_gone = json.loads(json.dumps(self.cfg)); del key_only_gone["free_tier_guard"]["contextdev"]["live_run_allowed"]   # valid record, key missing
        for name, cfg in (("key deleted, null record", no_key), ("block removed", no_block), ("free_tier_guard removed", no_ftg), ("key deleted, valid record", key_only_gone)):
            self.calls.clear(); search = mock.Mock(side_effect=self.ad_search)
            with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network call")) as net:
                s = self.go(n=2, cfg=cfg, tos=valid_tos, adapters={"contextdev": {"search": search}})
            self.assertEqual((self.prov(s)["status"], search.call_count), ("skipped_no_vendor_confirmation", 0), name); net.assert_not_called()
            self.assertFalse(list(self.spend.glob("contextdev-*")), name); self.assertFalse((self.tmp / "runs").exists(), name)
            self.assertFalse(guard_mod.live_allowed(cfg, valid_tos), name)
    def test_r26_c07_each_gate_term_alone_fails_closed(self):   # R26 (1) / C07: each term of the runner's live-gate condition is load-bearing on its own
        # (a) LIVE_GATED alone: contextdev with no guard block and a tos entry that lacks the required flag is still skipped
        no_block = json.loads(json.dumps(self.cfg)); del no_block["free_tier_guard"]["contextdev"]
        no_flag = {"contextdev": {k: v for k, v in self.tos["contextdev"].items() if k != gates_mod.REQUIRED_FLAG}}
        search = mock.Mock(side_effect=self.ad_search)
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network call")) as net:
            s = self.go(n=2, cfg=no_block, tos=no_flag, adapters={"contextdev": {"search": search}})
        self.assertEqual((self.prov(s)["status"], search.call_count), ("skipped_no_vendor_confirmation", 0)); net.assert_not_called()
        self.assertIn("config free_tier_guard.contextdev", self.prov(s)["reason"])
        # (b) tos required flag alone: a provider outside LIVE_GATED, valid tos record, no guard block, is skipped by the config-side gate
        other = {"id": "mockp", "categories": ["search"], "key_env": "K"}; pricing = self.pricing | {"mockp": {"search": mocks.pricing_fixture("credits")}}
        tos = self.tos | {"mockp": self.tos["contextdev"] | {gates_mod.REQUIRED_FLAG: True, "vendor_confirmation": dict(MOCK_CONFIRMATION)}}
        mp = mocks.MockAdapters(); s = self.go(providers=[other], cfg=self.cfg, tos=tos, pricing=pricing, env={"K": "x"}, adapters=mp.table("mockp", ["search"]))
        self.assertEqual(self.prov(s, "mockp")["status"], "skipped_no_vendor_confirmation"); self.assertIn("config free_tier_guard.mockp", self.prov(s, "mockp")["reason"])
        s = self.go(providers=[other], cfg=self.cfg, tos=self.tos | {"mockp": tos["mockp"] | {gates_mod.REQUIRED_FLAG: False}}, pricing=pricing, env={"K": "x"}, adapters=mp.table("mockp", ["search"]))
        self.assertEqual(self.prov(s, "mockp")["status"], "ran")   # positive control: without the flag the same provider runs
    def test_r22_bad_config_record_zero_live_calls(self):   # R21 (1) / R22: "yes", {} or a partial config record -> live_allowed False and the provider/http client is never invoked
        for bad in ("yes", {}, {"date": "2026-11-01", "sender": "x@context.dev"}, {k: v for k, v in MOCK_CONFIRMATION.items() if k != "recorded_pr"}):
            cfg = self.cfg_with(live_run_allowed=True, vendor_confirmation=bad)
            self.assertFalse(guard_mod.live_allowed(cfg, self.tos), bad)
            search, scrape = mock.Mock(side_effect=self.ad_search), mock.Mock(side_effect=self.ad_scrape)
            with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network call")) as net:
                s = self.go(n=2, kinds=("search", "scrape"), cfg=cfg, adapters={"contextdev": {"search": search, "scrape": scrape}})
            self.assertEqual([p["status"] for p in s["providers"]], ["skipped_no_vendor_confirmation"] * 2, bad)
            search.assert_not_called(); scrape.assert_not_called(); net.assert_not_called(); self.assertEqual(self.calls, [], bad)
            self.assertFalse(list(self.spend.glob("contextdev-*")), bad)
        cfg = self.cfg_with(live_run_allowed=True, vendor_confirmation=dict(MOCK_CONFIRMATION)); search = mock.Mock(side_effect=self.ad_search)   # positive control
        self.assertTrue(guard_mod.live_allowed(cfg, self.tos))
        self.go(n=2, cfg=cfg, adapters={"contextdev": {"search": search}}); self.assertEqual(search.call_count, 2)
    def test_ac6a_default_config_zero_calls(self):   # AC6a (a): committed gate, key set
        cfg = mocks.mock_cfg(CFG)
        s = self.go(n=3, kinds=("search", "scrape"), cfg=cfg)
        self.assertEqual([p["status"] for p in s["providers"]], ["skipped_no_vendor_confirmation"] * 2); self.assertEqual(self.calls, [])
        self.assertFalse(list(self.spend.glob("contextdev-*"))); self.assertFalse((self.tmp / "runs").exists())
    def test_ac6a_flag_true_confirmation_null_zero_calls(self):   # AC6a (b)
        s = self.go(cfg=self.cfg_with(vendor_confirmation=None))
        self.assertEqual((self.prov(s)["status"], self.calls), ("skipped_no_vendor_confirmation", []))
        s = self.go(cfg=self.cfg_with(live_run_allowed=False)); self.assertEqual((self.prov(s)["status"], self.calls), ("skipped_no_vendor_confirmation", []))
    def test_ac6a_both_set_adapter_reachable(self):   # AC6a (c)
        s = self.go(n=2); self.assertEqual((self.prov(s)["status"], len(self.calls)), ("ran", 2))
    def test_ac6a_gate_does_not_stop_other_providers(self):
        other = {"id": "mockp", "categories": ["search"], "key_env": "K"}; pricing = self.pricing | {"mockp": {"search": mocks.pricing_fixture("credits")}}
        s = self.go(providers=self.prov_def() + [other], cfg=mocks.mock_cfg(CFG), pricing=pricing, env={"K": "x", "CONTEXT_DEV_API_KEY": "mock-key"},
                    adapters={"contextdev": {"search": self.ad_search}} | mocks.MockAdapters().table("mockp", ["search"]))
        self.assertEqual((self.prov(s)["status"], self.prov(s, "mockp")["status"], self.calls), ("skipped_no_vendor_confirmation", "ran", []))
    def test_ac25_monday_news_job_contextdev_sits_out(self):   # AC25: B.main(), committed config, key set, any network call fails the test
        import shutil
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        root = mktmp(self) / "site"
        shutil.copytree(SITE / "bench", root / "bench", ignore=shutil.ignore_patterns("__pycache__")); shutil.copytree(SITE / "data", root / "data")
        (root / "collectors").mkdir(); shutil.copy(SITE / "collectors" / "providers.json", root / "collectors")
        news = sorted(f.stem for f in (root / "bench/sets").glob("news_*.jsonl"))[-1]
        snap = lambda: sorted((str(f.relative_to(root)), hashlib.sha256(f.read_bytes()).hexdigest()) for d in ("data", "bench/runs", "bench/spend") if (root / d).exists() for f in (root / d).rglob("*") if f.is_file())
        before = snap(); printed = []
        env = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_API_TOKEN", "_TOKEN", "_KEY"))} | {"CONTEXT_DEV_API_KEY": "mock-dummy"}
        with mock.patch.object(B, "ROOT", root), mock.patch.object(B, "BENCH", root / "bench"), mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(sys, "argv", ["benchmark.py", "--mode", "full", "--only-providers", "contextdev", "--only-sets", news]), \
             mock.patch("urllib.request.urlopen", side_effect=AssertionError("network call")), mock.patch.object(B, "contextdev_meter", side_effect=AssertionError("meter call")), \
             mock.patch("builtins.print", side_effect=lambda *a, **k: printed.append(" ".join(map(str, a)))):
            self.assertEqual(B.main(), 0)
        self.assertEqual(snap(), before)   # no run-log lines, no counter, no latest.json change
        sk = [json.loads(l) for l in printed if l.startswith("{")]
        self.assertEqual([(p["provider"], p["status"]) for p in sk], [("contextdev", "skipped_no_vendor_confirmation")] * 2)
        self.assertFalse(list((root / "bench/runs").rglob("contextdev")) and [d for d in (root / "bench/runs").rglob("contextdev") if d not in set((SITE / "bench/runs").rglob("contextdev"))])
        self.assertFalse(list((root / "data/spend").glob("contextdev-*")))

class ContextDevGuard(ContextDevBase):
    def test_ac11_stop_at_exactly_900_from_config(self):   # AC11
        self.assertEqual(CFG["free_tier_guard"]["contextdev"]["stop_at_credits"], 900)
    def test_ac11_edge_899_plus_scrape_runs(self):   # AC11
        self.seed(899); s = self.go(n=1, kinds=("scrape",)); p = self.prov(s)
        self.assertEqual((p["status"], len(self.calls)), ("ran", 1)); self.assertEqual(self.counter()["attempted"], 900)
    def test_ac11_edge_899_plus_search_markdown_stops(self):   # AC11: search 10 results with markdown = 2 credits
        self.seed(899)
        with mock.patch.object(guard_mod, "contextdev_request", return_value={"max_results": 10, "markdown": True}):
            p = self.prov(self.go(n=1))
        self.assertEqual((p["status"], self.calls, p["credits_used"], p["credits_stop_at"]), ("stale_quota", [], 899, 900)); self.assertEqual(self.counter()["attempted"], 899)
    def test_ac11_at_900_any_call_stops_before_adapter(self):   # AC11
        self.seed(900)
        for kind in ("search", "scrape"):
            p = self.prov(self.go(n=1, kinds=(kind,))); self.assertEqual((p["status"], self.calls), ("stale_quota", []), kind)
        self.assertEqual(self.counter()["attempted"], 900)
    def test_ac11_guard_reads_attempts_not_billed(self):   # AC11 / R1
        self.seed(900, billed=0); self.assertEqual(self.prov(self.go(n=1))["status"], "stale_quota"); self.assertEqual(self.calls, [])
        self.seed(0, billed=900); p = self.prov(self.go(n=1)); self.assertEqual((p["status"], len(self.calls)), ("ran", 1))
    def test_ac12_persistence_across_runs_same_window(self):   # AC12
        self.go(n=3); self.assertEqual(self.counter()["attempted"], 3)
        self.go(n=3, ledger="ledger2.json", ts="2026-10-28T10:00:00+00:00", run_date="2026-10-28"); self.assertEqual(self.counter()["attempted"], 6)
    def test_ac13_anchor_window_start_function(self):   # AC13
        A = lambda s, d: guard_mod.anchor_window_start(dt.datetime.fromisoformat(s), d)
        self.assertEqual(A("2026-10-14T12:00:00+00:00", 15), dt.date(2026, 9, 15)); self.assertEqual(A("2026-10-15T00:00:00+00:00", 15), dt.date(2026, 10, 15))
        self.assertEqual(A("2026-11-01T00:30:00+00:00", 15), dt.date(2026, 10, 15))   # the 1st does not reset
        self.assertEqual(A("2026-11-30T12:00:00+00:00", 31), dt.date(2026, 11, 30)); self.assertEqual(A("2026-11-29T12:00:00+00:00", 31), dt.date(2026, 10, 31))
        self.assertEqual(A("2026-12-01T00:00:00+00:00", 31), dt.date(2026, 11, 30)); self.assertEqual(A("2027-02-28T09:00:00+00:00", 31), dt.date(2027, 2, 28))
        self.assertEqual(A("2027-01-05T09:00:00+00:00", 15), dt.date(2026, 12, 15))   # year boundary
        self.assertEqual(A("2026-10-31T23:30:00-05:00", 1), dt.date(2026, 11, 1))   # UTC date of the timestamp
        with self.assertRaises(guard_mod.AnchorNotConfigured): A("2026-10-14T12:00:00+00:00", None)
    def test_ac13_run_on_14th_continues_old_window(self):   # AC13
        self.seed(100, window="2026-09-15"); s = self.go(n=3, ts="2026-10-14T10:00:00+00:00")
        self.assertEqual(self.counter("2026-09-15")["attempted"], 103); self.assertFalse((self.spend / "contextdev-2026-10-15.json").exists())
    def test_ac13_run_on_15th_starts_new_window_at_zero(self):   # AC13
        self.seed(850, window="2026-09-15"); before = (self.spend / "contextdev-2026-09-15.json").read_bytes()
        s = self.go(n=3, ts="2026-10-15T00:30:00+00:00", run_date="2026-10-15"); self.assertEqual(self.prov(s)["status"], "ran")
        self.assertEqual(self.counter("2026-10-15")["attempted"], 3); self.assertEqual((self.spend / "contextdev-2026-09-15.json").read_bytes(), before)
    def test_ac13_run_on_the_1st_does_not_reset(self):   # AC13
        self.seed(150); s = self.go(n=3, ts="2026-11-01T00:30:00+00:00", run_date="2026-11-01")
        self.assertEqual(self.counter()["attempted"], 153); self.assertFalse((self.spend / "contextdev-2026-11-01.json").exists())
    def test_ac13_anchor_31_clamps_in_runner(self):   # AC13
        cfg = self.cfg_with(grant_anchor_day=31)
        self.go(n=2, cfg=cfg, ts="2026-11-30T10:00:00+00:00", run_date="2026-11-30"); self.assertEqual(self.counter("2026-11-30")["attempted"], 2)
        self.go(n=2, cfg=cfg, ts="2026-12-01T10:00:00+00:00", run_date="2026-12-01", ledger="l2.json"); self.assertEqual(self.counter("2026-11-30")["attempted"], 4)
    def test_ac13_anchor_null_never_resets_and_logs(self):   # AC13
        cfg = self.cfg_with(grant_anchor_day=None)
        s1 = self.go(n=3, cfg=cfg, ts="2026-10-14T10:00:00+00:00"); s2 = self.go(n=3, cfg=cfg, ts="2026-12-15T10:00:00+00:00", run_date="2026-12-15", ledger="l2.json")
        f = self.spend / "contextdev-unanchored.json"; self.assertEqual(json.loads(f.read_text())["attempted"], 6)
        self.assertEqual(sorted(x.name for x in self.spend.glob("contextdev-*.json")), ["contextdev-unanchored.json"])
        for s in (s1, s2): self.assertIn("grant anchor not configured", self.prov(s)["guard_note"])
        self.seed(0); self.assertNotIn("guard_note", self.prov(self.go(n=1, ts="2026-10-20T10:00:00+00:00", ledger="l3.json")))   # anchor set: no note
    def test_ac14_corrupt_unreadable_locked_missing_fail_closed(self):   # AC14
        f = self.spend / f"contextdev-{self.W}.json"; import fcntl
        def corrupt(): self.seed(0); f.write_text("{not json")
        def bad_fields(): self.seed(0); f.write_text(json.dumps({"provider": "contextdev", "window": self.W, "attempted": -1, "billed": 0}))
        def wrong_window(): self.seed(0); f.write_text(json.dumps({"provider": "contextdev", "window": "2026-09-15", "attempted": 0, "billed": 0}))
        def missing_but_expected(): self.seed(0, window="2026-11-15")   # a later window exists but this one's file is gone
        for name, setup in (("corrupt", corrupt), ("bad fields", bad_fields), ("wrong window", wrong_window), ("missing", missing_but_expected)):
            setup(); self.calls.clear(); p = self.prov(self.go(n=2))
            self.assertEqual((p["status"], self.calls), ("stale_budget", []), name); self.assertIn("credit counter unreadable; failing closed", p["reason"], name)
            for x in self.spend.glob("contextdev-*.json"): x.unlink()
        self.seed(0); cfg = self.cfg_with(lock_timeout_s=0.2); lk = open(self.spend / ".contextdev.lock", "a"); fcntl.flock(lk, fcntl.LOCK_EX)
        try: p = self.prov(self.go(n=2, cfg=cfg))
        finally: lk.close()
        self.assertEqual((p["status"], self.calls), ("stale_budget", [])); self.assertIn("credit counter unreadable; failing closed", p["reason"])
    def test_ac14_permission_error_and_missing_dir(self):   # AC14
        self.seed(5); w = dt.date(2026, 10, 15)
        with mock.patch("pathlib.Path.read_text", side_effect=PermissionError("denied")):
            with self.assertRaises(guard_mod.GuardUnreadable): guard_mod.CreditGuard(self.spend, "contextdev", w, 900)
        with self.assertRaises(guard_mod.GuardUnreadable): guard_mod.CreditGuard(self.tmp / "nope", "contextdev", w, 900)
        if os.geteuid() != 0:
            f = self.spend / f"contextdev-{self.W}.json"; f.chmod(0)
            try: p = self.prov(self.go(n=1))
            finally: f.chmod(0o600)
            self.assertEqual((p["status"], self.calls), ("stale_budget", []))
        with mock.patch.object(guard_mod.CreditGuard, "_write", side_effect=OSError("disk")):   # the attempt cannot be counted: stop, fail closed
            p = self.prov(self.go(n=2, ledger="l9.json", ts="2026-12-20T10:00:00+00:00"))
        self.assertEqual(p["status"], "stale_budget")
    def test_ac15_clean_stop_writes_lines_and_others_run(self):   # AC15
        self.seed(897); other = {"id": "mockp", "categories": ["search"], "key_env": "K"}
        pricing = self.pricing | {"mockp": {"search": mocks.pricing_fixture("credits")}}
        s = self.go(n=5, providers=self.prov_def() + [other], pricing=pricing, adapters={"contextdev": {"search": self.ad_search}} | mocks.MockAdapters().table("mockp", ["search"]),
                    env={"K": "x", "CONTEXT_DEV_API_KEY": "mock-key"})
        p = self.prov(s); self.assertEqual((p["status"], p["calls"], len(self.calls)), ("stale_quota", 3, 3)); self.assertEqual((p["credits_used"], p["credits_stop_at"]), (900, 900))
        self.assertIn("900", p["reason"]); self.assertEqual(self.prov(s, "mockp")["status"], "ran")
        log = self.lines(); self.assertEqual(len(log), 3); self.assertTrue(all(r["run_status"] == "stale_quota" for r in log))
        self.assertTrue((self.tmp / "runs/2026-10-26/_run-offset0.json").exists())
    def test_ac16_no_paid_path(self):   # AC16
        self.assertIs(CFG["free_tier_guard"]["contextdev"]["paid_tier"], False)
        with self.assertRaises(runner.CapViolation): self.go(cfg=self.cfg_with(paid_tier=True))
        self.assertEqual(self.calls, [])
        (self.tmp / "ledger.json").write_text(json.dumps({"month": "2026-10", "used": {"contextdev": 999}, "over_free_units": 0}))
        with mock.patch.object(runner.guard, "contextdev_billed", return_value=5):
            with self.assertRaises(runner.CapViolation): self.go(n=1)   # billed credits past quota_credits (1000)
        for f in (BENCH / "awibench/runner.py", BENCH / "awibench/guard.py", SITE / "collectors/benchmark.py"):
            self.assertNotRegex(f.read_text(), r"(?i)paid_key|CONTEXT_DEV_PAID|CONTEXT_DEV_API_KEY_PAID|upgrade_plan")
    def test_ac16_ledger_quota_follows_guard_quota_credits(self):
        p = self.prov(self.go(n=5, cfg=self.cfg_with(quota_credits=2))); self.assertEqual((p["status"], p["quota"]), ("stale_quota", 2))

class ContextDevCredits(ContextDevBase):
    def test_ac17c_credit_cost_table(self):   # AC17c (F4)
        c = guard_mod.contextdev_credits
        self.assertEqual(c("scrape", {}, None), 1); self.assertEqual(c("scrape", {"browser_actions": True}, None), 2)
        self.assertEqual(c("search", {"max_results": 10}, None), 1); self.assertEqual(c("search", {"max_results": 11}, None), 2)
        self.assertEqual(c("search", {"max_results": 10, "markdown": True}, None), 2); self.assertEqual(c("search", {"max_results": 10, "markdown": True, "highlights": True}, None), 3)
        self.assertEqual(c("search", {"max_results": 10}, 7), 1); self.assertEqual(c("search", {"max_results": 10, "markdown": True}, 25), 6)   # billed on returned count
        with self.assertRaises(ValueError): c("browser", {}, None)

class ContextDevBilling(ContextDevBase):
    def one(self, fn, kind="search", n=1):
        s = self.go(n=n, kinds=(kind,), adapters={"contextdev": {kind: fn}}); return self.prov(s), self.lines(kind), self.counter()
    def test_ac17_function_table(self):   # AC17 (function level)
        b = guard_mod.contextdev_billed
        for st, want in ((200, 3), (201, 3), (404, 3), (400, 0), (422, 0), (401, 0), (403, 0), (402, 0), (408, 0), (429, 0), (500, 0), (502, 0), (503, 0), (None, 0)): self.assertEqual(b(st, {}, 3), want, st)
        self.assertEqual(b(402, {"error_code": "USAGE_EXCEEDED"}, 3), 0); self.assertEqual(b(401, {"error_code": "USAGE_EXCEEDED"}, 3), 0)
    def test_ac17_200_bills_credits(self):   # AC17
        def f(key, item): return {"http_status": 200, "billed_units": 1, "results": [{"url": f"https://a.example/{i}"} for i in range(10)]}
        p, L, c = self.one(f); self.assertEqual((L[0]["billed_units"], c["billed"], c["attempted"]), (1, 1, 1))
        p, L, c = self.one(lambda k, i: {"http_status": 200, "text": "x", "html": "x"}, kind="scrape"); self.assertEqual((L[0]["billed_units"], c["billed"]), (1, 2))
    def test_ac17_404_billed_scored_fail(self):   # AC17
        p, L, c = self.one(http_err(404)); self.assertEqual((L[0]["billed_units"], L[0]["status"], c["billed"]), (1, "fail", 1))
    def test_ac17_400_422_validation_unbilled(self):   # AC17
        for code in (400, 422):
            p, L, c = self.one(http_err(code)); self.assertEqual((L[-1]["billed_units"], L[-1]["status"]), (0, "fail"), code); self.assertEqual(p["status"].split("_")[0], "stale")
        self.assertEqual(self.counter()["billed"], 0)
    def test_ac17_401_403_unbilled_auth_error_stop(self):   # AC17
        for code in (401, 403):
            self.calls.clear(); s = self.go(n=4, adapters={"contextdev": {"search": http_err(code, "bad key")}}, ledger=f"l{code}.json"); p = self.prov(s); L = self.lines()
            self.assertEqual((p["status"], p["calls"], L[-1]["billed_units"], L[-1]["status"]), ("stale_auth_error", 1, 0, "auth_error"), code)
    def test_ac17_408_unbilled(self):   # AC17
        p, L, c = self.one(http_err(408, "timeout")); self.assertEqual((L[0]["billed_units"], L[0]["status"], c["attempted"], c["billed"]), (0, "fail", 1, 0))
    def test_ac17_429_unbilled_rate_limited(self):   # AC17
        p, L, c = self.one(http_err(429)); self.assertEqual((L[0]["billed_units"], c["billed"]), (0, 0)); self.assertTrue(runner.is_rate_limited(L[0]))
    def test_ac17_5xx_unbilled(self):   # AC17
        for code in (500, 502, 503):
            p, L, c = self.one(http_err(code)); self.assertEqual((L[-1]["billed_units"], L[-1]["status"]), (0, "fail"), code); self.assertFalse(runner.is_rate_limited(L[-1]))
            for x in self.spend.glob("contextdev-*.json"): x.unlink()
            (self.tmp / "ledger.json").unlink(missing_ok=True)
    def test_ac17_no_response_unbilled(self):   # AC17
        import urllib.error
        def f(key, item): raise urllib.error.URLError("connection refused")
        p, L, c = self.one(f); self.assertEqual((L[0]["billed_units"], L[0]["status"], c["attempted"], c["billed"]), (0, "unbilled_no_response", 1, 0))
    def test_ac17a_429_retry_after_waits_and_retries_once(self):   # AC17a
        first = [True]
        def fn(key, item):
            if first[0]: first[0] = False; self.calls.append(item["id"]); raise RuntimeError("HTTP 429: Retry-After: 1; concurrency")
            return self.ad_search(key, item)
        made = []; real = guard_mod.CreditGuard
        with mock.patch.object(runner.guard, "CreditGuard", side_effect=lambda *a, **k: made.append(1) or real(*a, **k)):
            s = self.go(n=5, adapters={"contextdev": {"search": fn}})
        self.assertEqual(self.sleeps, [1]); L = self.lines(); self.assertEqual(len(L), 6)   # item 0 twice, items 1-4 once
        self.assertEqual([r["item_id"] for r in L[:2]], [self.rows[0]["id"]] * 2); self.assertEqual((L[0]["http_status"], L[0]["status"], L[1]["status"]), (None, "fail", "fail"))
        self.assertTrue(runner.is_rate_limited(L[0])); self.assertEqual(self.calls.count(self.rows[0]["id"]), 2)
        self.assertEqual(len(made), 6); self.assertEqual(self.counter()["attempted"], 6)   # each attempt its own guard entry
        self.assertEqual(self.prov(s)["status"], "ran")
    def test_ac17a_second_429_is_not_retried_again(self):   # AC17a
        def fn(key, item): self.calls.append(item["id"]); raise RuntimeError("HTTP 429: Retry-After: 1; busy")
        self.go(n=1, adapters={"contextdev": {"search": fn}}); self.assertEqual((len(self.calls), self.sleeps), (2, [1]))
    def test_ac17a_429_without_retry_after_is_not_retried(self):   # AC17a / AC18 support
        self.go(n=1, adapters={"contextdev": {"search": http_err(429, "busy")}}); self.assertEqual(self.sleeps, []); self.assertEqual(self.counter()["attempted"], 1)
    def test_ac17a_retry_blocked_by_guard_stops_clean(self):   # AC17a + AC11
        self.seed(899)
        def fn(key, item): self.calls.append(item["id"]); raise RuntimeError("HTTP 429: Retry-After: 1; busy")
        p = self.prov(self.go(n=3, adapters={"contextdev": {"search": fn}})); self.assertEqual((p["status"], len(self.calls), self.sleeps), ("stale_quota", 1, [1]))
    def test_ac17b_quota_stop_402_and_401_usage_exceeded(self):   # AC17b
        other = {"id": "mockp", "categories": ["search"], "key_env": "K"}; pricing = self.pricing | {"mockp": {"search": mocks.pricing_fixture("credits")}}
        for code in (402, 401):
            self.calls.clear(); fn = http_err(code, '{"error_code":"USAGE_EXCEEDED"}')
            s = self.go(n=4, providers=self.prov_def() + [other], pricing=pricing, env={"K": "x", "CONTEXT_DEV_API_KEY": "mock-key"}, ledger=f"l{code}.json",
                        adapters={"contextdev": {"search": fn}} | mocks.MockAdapters().table("mockp", ["search"]), run_date=f"2026-10-2{code % 10}")
            p = self.prov(s); self.assertEqual((p["status"], p["calls"]), ("stale_quota", 1), code); self.assertEqual(self.sleeps, [])   # no retry, not auth_error
            self.assertIn("USAGE_EXCEEDED", p["reason"]); self.assertEqual(self.prov(s, "mockp")["status"], "ran")
            L = read_jsonl(self.tmp / f"runs/2026-10-2{code % 10}/contextdev/search_evergreen.jsonl"); self.assertEqual((len(L), L[0]["billed_units"], L[0]["status"]), (1, 0, "quota_stop"))
        self.assertEqual(self.counter()["billed"], 0)
    def test_ac18_guard_counts_every_attempt_billed_counts_less(self):   # AC18
        import urllib.error
        def md(kind, item): return {"max_results": 10, "markdown": True} if (kind == "search" and item["id"] == self.rows[0]["id"]) else ({"max_results": 10} if kind == "search" else {})
        def s200(key, item): return {"http_status": 200, "billed_units": 2, "results": [{"url": f"https://a.example/{i}"} for i in range(10)]}
        def sc404(key, item): raise RuntimeError("HTTP 404: not found")
        seqs = {"search": iter([s200, http_err(429, "busy")]), "scrape": iter([sc404, http_err(500)])}
        def mk(kind):
            def fn(key, item): return next(seqs[kind])(key, item)
            return fn
        with mock.patch.object(guard_mod, "contextdev_request", side_effect=md):
            self.go(n=2, kinds=("search", "scrape"), adapters={"contextdev": {"search": mk("search"), "scrape": mk("scrape")}})
        c = self.counter(); self.assertEqual((c["attempted"], c["billed"]), (5, 3))   # 2 + 1 + 1 + 1 attempted; 2 + 1 billed
        self.assertEqual(json.loads((self.tmp / "ledger.json").read_text())["used"]["contextdev"], 3)
    def test_ac19_same_sets_and_cost_columns_hand_calc(self):   # AC19
        fx = {"model": "credits", "credits_per_call": {"search": 1, "scrape": 1}, "free_tier": {"units_per_month": 1000, "needs_card": False, "pool": "contextdev", "note": "fixture"},
              "tiers": {"entry": {"name": "Fixture entry", "usd_per_credit": 0.002}, "volume": {"name": "Fixture volume", "usd_per_credit": 0.001}}}
        pricing = self.pricing | {"contextdev": {"search": fx, "scrape": fx}}
        s = self.go(n=5, kinds=("search", "scrape"), pricing=pricing)
        self.assertEqual({(p["category"], p["status"]) for p in s["providers"] if p["provider"] == "contextdev"}, {("search", "ran"), ("scrape", "ran")})
        for kind, set_name in (("search", "search_evergreen"), ("scrape", "scrape_urls")):
            L = self.lines(kind); self.assertEqual(len(L), 5, set_name)   # contextdev takes the same frozen sets; news_* sets map to kind search too (runner.set_kind)
            c = cost.cost_columns(fx, L); scored = [l for l in L if l["status"] in ("pass", "fail")]
            usd_entry = sum(l["billed_units"] for l in scored) * 0.002
            self.assertAlmostEqual(c["entry_tier_cost_per_1k"], 1000 * usd_entry / len(scored)); self.assertAlmostEqual(c["volume_tier_cost_per_1k"], 1000 * (usd_entry / 2) / len(scored))
            self.assertAlmostEqual(c["entry_tier_cost_per_1k"], 2.0)   # hand calc: 5 credits x $0.002 / 5 calls x 1000
        self.assertEqual(runner.set_kind("news_2026-44"), "search")
        # free credits never lower cost: the same lines priced with a free_tier note changed give the same columns
        fx2 = json.loads(json.dumps(fx)); fx2["free_tier"]["units_per_month"] = 10**9
        self.assertEqual(cost.cost_columns(fx, self.lines())["entry_tier_cost_per_1k"], cost.cost_columns(fx2, self.lines())["entry_tier_cost_per_1k"])
    def test_ac20_display_rounding_is_existing_half_up(self):   # AC20
        src = (BENCH / "awibench/guard.py").read_text(); self.assertNotRegex(src, r"\bround\(|quantize|ROUND_|Decimal")   # no new rounding code in the contextdev paths
        self.assertEqual(tables.cost.fmt(2.0), "$2.00"); self.assertEqual(tables.cost.fmt(None), "n/a")

class ContextDevConcurrency(ContextDevBase):
    def test_ac10a_config_max_concurrent_is_one(self):   # AC10 (a)
        self.assertEqual(CFG["free_tier_guard"]["contextdev"]["max_concurrent"], 1)
    def test_ac10b_adapter_invoked_through_semaphore(self):   # AC10 (b)
        import threading
        class Spy:
            def __init__(s): s.acq = s.rel = 0; s.s = threading.BoundedSemaphore(1)
            def acquire(s, *a): s.acq += 1; return s.s.acquire(*a)
            def release(s): s.rel += 1; s.s.release()
        spy = Spy(); held = []
        def fn(key, item): held.append(spy.acq - spy.rel); return {"http_status": 200, "billed_units": 1, "results": []}
        made = []
        with mock.patch.object(runner.guard, "make_semaphore", side_effect=lambda n: made.append(n) or spy): self.go(n=4, adapters={"contextdev": {"search": fn}})
        self.assertEqual((made, spy.acq, spy.rel, held), ([1], 4, 4, [1, 1, 1, 1]))
        spy = Spy()
        def boom(key, item): raise RuntimeError("HTTP 500: x")
        with mock.patch.object(runner.guard, "make_semaphore", return_value=spy): self.go(n=2, adapters={"contextdev": {"search": boom}}, run_date="2026-10-27")
        self.assertEqual((spy.acq, spy.rel), (2, 2))   # a raising adapter still releases
    def test_ac10c_semaphore_peak_in_flight_is_one(self):   # AC10 (c)
        import concurrent.futures, threading, time
        sem = guard_mod.make_semaphore(CFG["free_tier_guard"]["contextdev"]["max_concurrent"]); lock = threading.Lock(); cur = [0]; peak = [0]
        def fn(key, item):
            with lock: cur[0] += 1; peak[0] = max(peak[0], cur[0])
            time.sleep(0.02)
            with lock: cur[0] -= 1
            return {"http_status": 200}
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex: list(ex.map(lambda i: guard_mod.guarded_call(sem, fn, "k", {"id": i}), range(12)))
        self.assertEqual(peak[0], 1)

class ContextDevMeter(ContextDevBase):
    @staticmethod
    def _fake_urlopen(seen, resp):
        class R:
            status = 200
            def __init__(s, b): s.b = b
            def read(s): return json.dumps(s.b).encode()
            def __enter__(s): return s
            def __exit__(s, *a): return False
        def fake(req, timeout=None):
            seen.append(req)
            if isinstance(resp, Exception): raise resp
            return R(resp)
        return fake
    def test_ac26_guard_uses_max_of_meter_and_counter(self):   # AC26
        p = self.prov(self.go(n=2, meters={"contextdev": lambda: 900})); self.assertEqual((p["status"], self.calls), ("stale_quota", []))
        p = self.prov(self.go(n=1, meters={"contextdev": lambda: 899}, ledger="l2.json")); self.assertEqual((p["status"], len(self.calls)), ("ran", 1))
        self.calls.clear(); self.seed(900); p = self.prov(self.go(n=1, meters={"contextdev": lambda: 0}, run_date="2026-10-27", ledger="l3.json")); self.assertEqual((p["status"], self.calls), ("stale_quota", []))
    def test_ac26_unreadable_meter_fails_closed(self):   # AC26
        def boom(): raise RuntimeError("HTTP 500")
        for m in (boom, lambda: None, lambda: float("nan"), lambda: -1):
            self.calls.clear(); p = self.prov(self.go(n=2, meters={"contextdev": m}))
            self.assertEqual((p["status"], self.calls), ("stale_budget", [])); self.assertIn("usage meter unreadable; failing closed", p["reason"])
    def test_ac26_gap_logged(self):   # AC26
        self.seed(10); p = self.prov(self.go(n=2, meters={"contextdev": lambda: 25}))
        self.assertEqual((p["meter_credits"], p["counter_credits"], p["meter_minus_counter"]), (25, 11, 14)); self.assertEqual(self.counter()["attempted"], 12)
    def test_ac26_meter_client_is_get_only(self):   # AC26: no POST/PUT/PATCH/DELETE from the meter client
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B, urllib.error
        seen = []
        def run_with(resp):
            with mock.patch("urllib.request.urlopen", side_effect=self._fake_urlopen(seen, resp)): return B.contextdev_meter("secret-key-123")
        self.assertEqual(run_with({"credits_remaining": 600, "period": {"start": "2026-10-01", "credits_used": 400}, "next_refill": "2026-11-01"}), 400)
        r = seen[0]; self.assertEqual((r.get_method(), r.data, r.full_url), ("GET", None, "https://api.context.dev/v1/org/usage"))
        self.assertNotIn("secret-key-123", r.full_url); self.assertEqual(r.get_header("Authorization"), "Bearer secret-key-123")
        for bad in ({"period": None}, {"credits_remaining": 5}, {"period": {"credits_used": "x"}}, {"period": {"credits_used": -1}},
                    urllib.error.URLError("secret-key-123 down"), urllib.error.HTTPError("u", 403, "forbidden", {}, __import__("io").BytesIO(b"logs:read"))):
            self.assertIsNone(run_with(bad))
        self.assertTrue(seen); self.assertTrue(all(q.get_method() not in ("POST", "PUT", "PATCH", "DELETE") and q.get_method() == "GET" and q.data is None for q in seen))
        self.assertEqual(CFG["free_tier_guard"]["contextdev"]["meter"], "https://api.context.dev/v1/org/usage")
    def test_ac26_main_wires_contextdev_meter(self):   # AC26
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        seen = {}
        class Stop(Exception): pass
        def fake_run(**kw): seen.update(kw); raise Stop()
        env = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_API_TOKEN", "_TOKEN", "_KEY"))} | {"CONTEXT_DEV_API_KEY": "mock-dummy"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "argv", ["benchmark.py", "--mode", "smoke", "--only-providers", "contextdev"]), \
             mock.patch.object(B.runner, "run", fake_run), mock.patch.object(B, "contextdev_meter", return_value=42) as cm, \
             mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")), mock.patch("builtins.print"):
            with self.assertRaises(Stop): B.main()
            self.assertEqual(seen["meters"]["contextdev"](), 42); self.assertEqual(seen["guard_dir"], SITE / "data" / "spend")
        cm.assert_called_once_with("mock-dummy")

class ContextDevAdapterAndSecrets(ContextDevBase):
    def test_adapters_request_shape_and_retry_after(self):   # AC17a/AC17c support: adapter surface
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B, urllib.error, io
        seen = []
        resp = lambda req: {"results": [{"url": "https://a.example/", "title": "T", "description": "D", "publishedDate": "2026-10-25"}]} if req.full_url.endswith("search") else {"markdown": "hello", "html": "<p>hello</p>"}
        def fake(req, timeout=None):
            seen.append(req)
            class R:
                status = 200
                def read(s): return json.dumps(resp(req)).encode()
                def __enter__(s): return s
                def __exit__(s, *a): return False
            return R()
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            so = B._contextdev_search("k-123", {"query": "q"}); sc = B._contextdev_scrape("k-123", {"url": "https://x.example/"})
        self.assertEqual(so["results"], [{"url": "https://a.example/", "title": "T", "snippet": "D", "published_at": "2026-10-25"}]); self.assertEqual((so["http_status"], so["billed_units"], sc["billed_units"]), (200, 1, 1))
        self.assertEqual((sc["text"], sc["html"]), ("hello", "<p>hello</p>"))
        self.assertEqual([r.full_url for r in seen], ["https://api.context.dev/v1/web/search", "https://api.context.dev/v1/web/scrape"])
        for r in seen: self.assertEqual(r.get_header("Authorization"), "Bearer k-123"); self.assertNotIn("k-123", r.full_url)
        def h429(req, timeout=None): raise urllib.error.HTTPError(req.full_url, 429, "busy", {"Retry-After": "1"}, io.BytesIO(b"concurrency"))
        with mock.patch("urllib.request.urlopen", side_effect=h429):
            with self.assertRaises(RuntimeError) as cm: B._contextdev_search("k-123", {"query": "q"})
        self.assertIn("HTTP 429: Retry-After: 1;", str(cm.exception))
    def test_ac9_secret_canary_absent_everywhere(self):   # AC9
        import io, random, urllib.error, itertools, contextlib
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        canary = f"awi-canary-{random.SystemRandom().getrandbits(64):016x}"
        def echo_http(req, timeout=None): raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, io.BytesIO(f"echoed headers: Authorization: Bearer {canary}".encode()))
        def echo_429(req, timeout=None): raise urllib.error.HTTPError(req.full_url, 429, "busy", {"Retry-After": "1"}, io.BytesIO(f"echoed Authorization: Bearer {canary}".encode()))
        def echo_url(req, timeout=None): raise urllib.error.URLError(f"connect failed; sent Authorization: Bearer {canary}")
        def echo_generic(req, timeout=None): raise ValueError(f"bad request headers {dict(req.header_items())}")
        cyc = itertools.cycle([echo_http, echo_429, echo_url, echo_generic]); out, err = io.StringIO(), io.StringIO()
        def fake(req, timeout=None): return next(cyc)(req, timeout)
        errs = []
        with mock.patch.dict(os.environ, {"CONTEXT_DEV_API_KEY": canary}), mock.patch("urllib.request.urlopen", side_effect=fake), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            for f in (echo_http, echo_429, echo_url, echo_generic):
                try: B._contextdev_search(canary, {"query": "q"}); self.fail("no error")
                except RuntimeError as e: errs.append(str(e))
                try: B._contextdev_scrape(canary, {"url": "https://x.example/"})
                except RuntimeError as e: errs.append(str(e))
            s = self.go(n=6, adapters={"contextdev": B.ADAPTERS["contextdev"]}, env={"CONTEXT_DEV_API_KEY": canary}, meters={"contextdev": lambda: B.contextdev_meter(canary)})
            self.assertEqual(self.prov(s)["status"].split("_")[0], "stale")   # every call errored
        blobs = [out.getvalue(), err.getvalue(), json.dumps(s), "\n".join(errs)] + [f.read_text(errors="replace") for f in self.tmp.rglob("*") if f.is_file()]
        for b in blobs: self.assertNotIn(canary, b)
        self.assertTrue(any("***" in e for e in errs)); self.assertTrue(list((self.tmp / "runs").rglob("*.jsonl")) or list(self.tmp.rglob("*.json")))
        self.assertEqual(secrets_scan.scan(SITE), [])

class ContextDevPublish(ContextDevBase):
    P0 = {"model": "credits", "credits_per_call": {"search": 1, "scrape": 1}, "source_url": "https://www.context.dev/pricing", "checked_at": "2026-10-26",
          "free_tier": {"units_per_month": 1000, "needs_card": False, "pool": "contextdev"}, "tiers": {"entry": {"name": "E", "usd_per_credit": 0.004}, "volume": {"name": "NOT CAPTURED"}}}
    P1 = {"terms_url": "https://www.context.dev/term", "checked_at": "2026-10-26", "allowed": "yes", "verdict": "allowed", "full_read": True,
          "benchmarking": {"status": "allowed", "quote": "q"}, "publishing_results": {"status": "allowed", "quote": "q"}, "anti_bot_scraping": {"status": "unclear", "quote": "q"}}
    def _runs(self, tos=None, pricing=None):
        self.go(n=5, tos=tos, pricing=pricing); return self.tmp / "runs"
    def _built(self, runs, pricing, tos, cfg): return tables.build(runs, pricing, self.tmp / "sets", tos, cfg)
    def _rows(self, doc): return [r for t in doc["tables"].values() for r in t if r["provider"] == "contextdev"]
    def _gate_inputs(self): return self.pricing | {"contextdev": {"search": self.P0, "scrape": self.P0}}, self.tos | {"contextdev": self.P1}
    def test_ac23_p0_only_is_held(self):   # AC23
        runs = self._runs(); cfg = self.cfg | {"publish_hold": []}; p0, _ = self._gate_inputs()
        self.assertEqual(self._rows(self._built(runs, p0, self.tos, cfg)), []); self.assertFalse(tables.publish_gate("contextdev", p0, self.tos)[0])
        self.assertTrue((runs / "2026-10-26/contextdev/search_evergreen.jsonl").exists())   # run logs written in every case
    def test_ac23_p1_only_is_held(self):   # AC23
        runs = self._runs(); cfg = self.cfg | {"publish_hold": []}; _, t1 = self._gate_inputs()
        self.assertEqual(self._rows(self._built(runs, self.pricing, t1, cfg)), []); self.assertFalse(tables.publish_gate("contextdev", self.pricing, t1)[0])
        self.assertTrue((runs / "2026-10-26/contextdev/search_evergreen.jsonl").exists())
    def test_ac23_neither_is_held(self):   # AC23
        runs = self._runs(); cfg = self.cfg | {"publish_hold": []}
        self.assertEqual(self._rows(self._built(runs, self.pricing, self.tos, cfg)), []); self.assertFalse(tables.publish_gate("contextdev", self.pricing, self.tos)[0])
        self.assertTrue((runs / "2026-10-26/contextdev/search_evergreen.jsonl").exists())
    def test_ac23_both_passes_the_gate(self):   # AC23 (still held in this build by AC24)
        runs = self._runs(); p0, t1 = self._gate_inputs()
        self.assertTrue(tables.publish_gate("contextdev", p0, t1)[0]); self.assertTrue(self._rows(self._built(runs, p0, t1, self.cfg | {"publish_hold": []})))
        for bad in ({"full_read": False}, {"allowed": None}, {"verdict": "unverified"}, {"publishing_results": {"status": "forbidden", "quote": "q"}}, {"benchmarking": {"status": "forbidden", "quote": "q"}}):
            self.assertFalse(tables.publish_gate("contextdev", p0, self.tos | {"contextdev": self.P1 | bad})[0], bad)
        for bad in ({"source_url": "NOT CAPTURED"}, {"checked_at": ""}, {"tiers": {"entry": {"name": "E", "usd_per_credit": "NOT CAPTURED"}}}):
            self.assertFalse(tables.publish_gate("contextdev", {"contextdev": {"search": self.P0 | bad}}, t1)[0], bad)
    def test_ac23_committed_files_keep_gate_closed(self):   # AC23: real pricing.yaml + tos_matrix.yaml
        ok, why = tables.publish_gate("contextdev", self.pricing, self.tos); self.assertFalse(ok); self.assertEqual(why, "P0 pricing not passed; P1 terms not passed")
        self.assertIn("contextdev", tables.held_providers(CFG, self.pricing, self.tos))
    def test_ac24_publish_hold_holds_even_when_gate_passes(self):   # AC24
        runs = self._runs(); p0, t1 = self._gate_inputs(); self.assertEqual(CFG["publish_hold"], ["contextdev"])
        self.assertEqual(self._rows(self._built(runs, p0, t1, self.cfg)), [])   # gate passes, publish_hold still holds it
        self.assertTrue(self._rows(self._built(runs, self.pricing, self.tos, self.cfg | {"publish_hold": [], "publish_gate": []})))   # control: the logs do build a row
        self.assertIn("contextdev", tables.held_providers(self.cfg, p0, t1))
    def test_ac24_run_logs_cost_na_when_published_unheld(self):   # AC2/AC24 support: no price -> cost n/a even if a row were built
        runs = self._runs(); L = read_jsonl(runs / "2026-10-26/contextdev/search_evergreen.jsonl"); self.assertEqual(len(L), 5)
        doc = self._built(runs, self.pricing, self.tos, self.cfg | {"publish_hold": [], "publish_gate": []})
        r = self._rows(doc)[0]; self.assertEqual((r["entry_tier_cost_per_1k"]["display"], r["volume_tier_cost_per_1k"]["display"]), ("n/a", "n/a"))
    def test_ac5_forbidden_publishing_results_or_benchmarking_excludes(self):   # AC5
        runs = self._runs()
        for clause in ("publishing_results", "benchmarking"):
            tos = self.tos | {"contextdev": self.tos["contextdev"] | {clause: {"status": "forbidden", "quote": "no comparisons"}}}
            doc = self._built(runs, self.pricing, tos, self.cfg | {"publish_hold": [], "publish_gate": []})
            self.assertEqual(self._rows(doc), []); ex = [e for e in doc["exclusions"] if e["provider"] == "contextdev"]
            self.assertEqual((len(ex), ex[0]["quote"], ex[0]["reason"]), (1, "no comparisons", f"terms forbid {clause.replace('_', ' ')}"))
        tos = self.tos | {"contextdev": self.tos["contextdev"] | {"publishing_results": {"status": "forbidden", "quote": "q"}}}
        self.calls.clear(); p = self.prov(self.go(n=2, tos=tos, run_date="2026-10-27")); self.assertEqual((p["status"], self.calls), ("excluded_tos_benchmarking", []))
        self.assertIn("terms forbid publishing results", p["reason"]); self.assertTrue(runner.benchmark_forbidden(tos["contextdev"])); self.assertFalse(runner.benchmark_forbidden(self.tos["contextdev"]))
        for f in (SITE / "data/benchmarks").glob("*"):   # nothing contextdev in the committed published data
            if f.is_file(): self.assertNotIn("contextdev", f.read_text(errors="replace").lower().replace("-", ""))
    def test_ac24_strip_held_and_fresh_data(self):   # AC24
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        row = lambda pid: {"provider": pid, "attempted": {"v": 3}}
        doc = {"tables": {"search": [row("contextdev"), row("exa")], "scrape": [], "browser": []}, "freshness": [{"provider": "contextdev"}], "not_run_yet": [{"provider": "contextdev"}],
               "skips": [{"provider": "contextdev"}, {"scope": "set", "set": "s"}], "run_summary": {"providers": [{"provider": "contextdev"}, {"provider": "exa"}]}}
        tables.strip_held(doc, {"contextdev"}); self.assertNotIn("contextdev", json.dumps(doc)); self.assertEqual(len(doc["skips"]), 1)
        s = {"providers": [{"provider": "contextdev", "status": "ran", "data_calls": 3}]}
        self.assertTrue(B.fresh_data(s)); self.assertFalse(B.fresh_data(s, {"contextdev"}))

class ContextDevDocs(unittest.TestCase):
    def test_ac28_docs_describe_the_provider_and_match_code(self):   # AC28
        docs = {n: (SITE / n).read_text() for n in ("README.md", "bench/README.md", "SPEC-bench.md")}
        for n, t in docs.items():
            for needle in ("Context.dev", "skipped_no_vendor_confirmation", "live_run_allowed", "grant-anchor", "exclude_from_also_priced", "publish_hold", "USAGE_EXCEEDED", "max_concurrent"):
                self.assertIn(needle, t, (n, needle))
        b = docs["bench/README.md"]
        for needle in ("900", "cap 1", "contextdev_credits", "contextdev_billed", "anchor_window_start", "Retry-After", "publish_gate", "ceil("):
            self.assertIn(needle, b, needle)
        g = CFG["free_tier_guard"]["contextdev"]
        self.assertEqual((g["max_concurrent"], g["stop_at_credits"], g["retry_after_429_s"]), (1, 900, 1))
        for t in docs.values(): self.assertNotRegex(t, r"also_priced_line|2-slot|max_concurrent\W+\(?2\)?|Monday fallback")   # stale rev-1/rev-2 wording


class VendorConfirmationGate(ContextDevBase):
    """AWI-PROVIDER-CONTEXTDEV-001 R10 (AC24a-24f). MOCK ONLY."""
    REQ = {"vendor_confirmation_required": True}
    FULL = {"date": "2026-11-01", "sender": "A Person, a@example.invalid", "quote": "you may publish", "evidence_path": "meta/x.md", "recorded_pr": "https://github.com/hazeydata/agentwebindex/pull/999"}
    def test_ac24a_truth_table(self):   # AC24a
        vc = gates_mod.vendor_confirmed
        self.assertFalse(vc(self.REQ | {"vendor_confirmation": None})); self.assertFalse(vc(self.REQ))
        self.assertFalse(vc(self.REQ | {"vendor_confirmation": {k: v for k, v in self.FULL.items() if k != "sender"}}))   # no sender
        self.assertFalse(vc(self.REQ | {"vendor_confirmation": self.FULL | {"date": ""}}))
        self.assertFalse(vc(self.REQ | {"vendor_confirmation": {k: v for k, v in self.FULL.items() if k not in ("quote", "evidence_path")}}))   # neither quote nor evidence
        self.assertFalse(vc(self.REQ | {"vendor_confirmation": "yes"}))
        self.assertTrue(vc(self.REQ | {"vendor_confirmation": self.FULL}))
        self.assertFalse(vc(self.REQ | {"vendor_confirmation": self.FULL | {"quote": None}}))   # R26: evidence_path alone (quote null) fails
        self.assertFalse(vc(self.REQ | {"vendor_confirmation": self.FULL | {"evidence_path": None}}))   # R26: quote alone (evidence_path null) fails
        self.assertTrue(vc({"vendor_confirmation_required": False})); self.assertTrue(vc({})); self.assertTrue(vc(None))
        real = load_yaml(BENCH / "tos_matrix.yaml")["providers"]
        old = [p for p in real if p not in ("contextdev", "keenable")]; self.assertEqual(len(old), 15)
        for pid in old:
            self.assertTrue(gates_mod.vendor_confirmed(real[pid]), pid); self.assertNotIn("vendor_confirmation_required", real[pid], pid)
            if pid != "contextdev": self.assertTrue(gates_mod.publishable(pid, self.pricing, real), pid)
    def test_ac24a_gate_holds_publishing(self):   # AC24a: a gated provider publishes nothing until the record is complete; full record passes this gate only
        runs = self.tmp / "runs"; f = runs / "2026-10-26" / "gatedp" / "search_evergreen.jsonl"; f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"ts": "2026-10-26T11:00:00+00:00", "status": "pass", "item_id": "i1", "set_sha": "x", "set_rows": 1, "latency_ms": 5, "billed_units": 1, "mock": True}) + "\n")
        base = {"terms_url": "https://x.example/t", "checked_at": "2026-10-25", "allowed": "yes"}
        def rows(entry): return [r for t in tables.build(runs, {}, None, {"gatedp": entry}, {})["tables"].values() for r in t]
        self.assertEqual(rows(base | self.REQ | {"vendor_confirmation": None}), [])
        self.assertEqual(rows(base | self.REQ | {"vendor_confirmation": {k: v for k, v in self.FULL.items() if k != "sender"}}), [])
        self.assertEqual(len(rows(base | self.REQ | {"vendor_confirmation": self.FULL})), 1)
        self.assertEqual(len(rows(base)), 1)   # not required: unaffected
    def test_ac24b_committed_files_closed(self):   # AC24b: the real committed files
        tos = load_yaml(BENCH / "tos_matrix.yaml")["providers"]
        for pid in ("keenable", "contextdev"):
            self.assertIs(tos[pid]["vendor_confirmation_required"], True); self.assertIsNone(tos[pid]["vendor_confirmation"]); self.assertFalse(gates_mod.vendor_confirmed(tos[pid]), pid)
        k = tos["keenable"]
        self.assertEqual((k["terms_url"], k["checked_at"], k["allowed"], k["benchmarking"]["status"]), ("https://keenable.ai/terms", "2026-10-25", "no", "forbidden"))
        self.assertEqual(k["benchmarking"]["quote"], "to any third party the results of any benchmark tests of the Service")
        self.assertFalse(gates_mod.publishable("contextdev", load_yaml(BENCH / "pricing.yaml")["providers"], tos))
        self.assertIsNone(CFG["free_tier_guard"]["contextdev"]["vendor_confirmation"])
    def test_ac24c_keenable_never_renders(self):   # AC24c: fixture logs with keenable rows + a pricing fixture entry; data JSON and a flattened CSV carry no keenable
        runs = self.tmp / "runs"
        for ps, st in (("keenable", "pass"), ("mockp", "pass")):
            for sname in ("search_evergreen", "news_2026-W43"):
                f = runs / "2026-10-26" / ps / f"{sname}.jsonl"; f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps({"ts": "2026-10-26T11:00:00+00:00", "status": st, "item_id": "i1", "set_sha": "x", "set_rows": 1, "latency_ms": 5, "billed_units": 1, "mock": True, "results": []}) + "\n")
        pricing = {"keenable": {"search": mocks.pricing_fixture("credits")}, "mockp": {"search": mocks.pricing_fixture("credits")}}
        tos = load_yaml(BENCH / "tos_matrix.yaml")["providers"]
        cfg = {"month1_free_tier_providers": ["keenable", "mockp"], "publish_hold": [], "publish_gate": []}
        summary = {"providers": [{"provider": "keenable", "category": "search", "status": "skipped_no_vendor_confirmation"}, {"provider": "mockp", "category": "search", "status": "ran"}]}
        for t in (tos, tos | {"keenable": tos["keenable"] | {"benchmarking": {"status": "allowed", "quote": "q"}, "allowed": "yes"}}):   # forbidden or not: still hidden
            doc = tables.build(runs, pricing, self.tmp / "sets", t, cfg); self.assertNotIn("keenable", json.dumps(doc).lower())   # build() alone, before strip_held
            doc["not_run_yet"] = tables.not_run_yet(summary, cfg, doc); doc["run_summary"] = summary; doc["skips"] = [{"provider": "keenable", "status": "x"}]
            tables.strip_held(doc, tables.held_providers(cfg, pricing, t))
            js = json.dumps(doc); csv = "\n".join(",".join(str(r[k]) for k in ("provider", "set", "run_date")) for tb in doc["tables"].values() for r in tb)
            for blob in (js, csv): self.assertNotIn("keenable", blob.lower())
            self.assertEqual({r["provider"] for tb in doc["tables"].values() for r in tb}, {"mockp"})   # others unaffected
            self.assertNotIn("keenable", [e["provider"] for e in doc["exclusions"]])
        # runner: a synthetic keenable run summary never reaches the published run_summary
        s = self.go(providers=[{"id": "keenable", "categories": ["search"], "key_env": "KEENABLE_API_KEY"}] + self.prov_def(), env={"KEENABLE_API_KEY": "dummy-not-a-key", "CONTEXT_DEV_API_KEY": "mock-key"})
        doc = {"tables": {}, "run_summary": s}; tables.strip_held(doc, tables.held_providers(cfg, pricing, tos)); self.assertNotIn("keenable", json.dumps(doc).lower())
    def _code_files(self, root):
        out = []
        for base in (root, root.parent / "agentwebindex-site"):
            if not base.is_dir(): continue
            for f in base.rglob("*"):
                if f.suffix in (".py", ".js", ".sh") and f.is_file() and not {"tests", "__pycache__", "node_modules", ".git", "dist"} & set(f.relative_to(base).parts): out.append(f)
        return out
    def test_ac24d_one_function(self):   # AC24d: this repo plus ../agentwebindex-site when present (skipped gracefully when absent)
        files = self._code_files(SITE)
        defs = [f for f in files if re.search(r"^\s*def vendor_confirmed\b", f.read_text(errors="replace"), re.M)]
        self.assertEqual([f.name for f in defs], ["gates.py"])
        self.assertEqual(len(re.findall(r"^\s*def vendor_confirmed\b", defs[0].read_text(), re.M)), 1)
        for f in files:   # the key is read only inside vendor_confirmed (gates.py); every other module goes through the function
            txt = f.read_text(errors="replace")
            if f.name == "gates.py": self.assertEqual(len(re.findall(r"vendor_confirmation_required", txt)), 2, "docstring + the one check"); continue
            self.assertNotRegex(txt, r"""["']vendor_confirmation_required["']""", str(f))
        for mod in ("tables.py", "guard.py", "runner.py"): self.assertRegex((BENCH / "awibench" / mod).read_text(), r"gates|vendor_confirmed", mod)
        self.assertRegex((SITE / "collectors/benchmark.py").read_text(), r"from awibench import .*\bgates\b")
        self.assertIs(tables.vendor_confirmed, gates_mod.vendor_confirmed); self.assertIs(guard_mod.vendor_confirmed, gates_mod.vendor_confirmed)
    def _cd_docs(self):   # AC24e fixtures: base = committed shape (nothing confirmed)
        import copy
        tos = {"providers": {"exa": {"allowed": "yes", "checked_at": "2026-10-01"},
                             "contextdev": {"terms_url": "https://www.context.dev/term", "checked_at": "2026-10-25", "vendor_confirmation_required": True,
                                            "benchmarking": {"status": "unclear", "quote": "No clause found in full read."}, "vendor_confirmation": None},
                             "keenable": {"allowed": "no", "vendor_confirmation_required": True, "vendor_confirmation": None,
                                          "benchmarking": {"status": "forbidden", "quote": "q"}}}}
        cfg = {"free_tier_guard": {"contextdev": {"stop_at_credits": 900, "live_run_allowed": False, "vendor_confirmation": None,
                                                  "anchor": {"date": "2026-10-01"}}}}
        base = {"bench/tos_matrix.yaml": tos, "bench/config.yaml": cfg}
        return base, copy.deepcopy(base)
    REC = {"date": "2026-10-30", "sender": "A, a@context.dev", "quote": "You may benchmark and publish.", "evidence_path": "cycles/x/evidence/reply.txt", "recorded_pr": "https://github.com/hazeydata/agentwebindex/pull/999"}
    def _flip(self, head, cfg_too=True):
        head["bench/tos_matrix.yaml"]["providers"]["contextdev"]["vendor_confirmation"] = dict(self.REC)
        if cfg_too: head["bench/config.yaml"]["free_tier_guard"]["contextdev"] |= {"live_run_allowed": True, "vendor_confirmation": dict(self.REC)}
        return head
    def test_r26_c07_confirmation_diff_rejects_guard_field_deletion(self):   # R26 / C07: deleting live_run_allowed, the record key or the block fails, flip or not
        cd = confirmation_diff; T, C = "bench/tos_matrix.yaml", "bench/config.yaml"; import copy
        for files in ([T, C], [T, C, "cycles/2026-10-25-provider-contextdev/decisions.md"]):
            for name, mut in (("live_run_allowed", lambda g: g["free_tier_guard"]["contextdev"].pop("live_run_allowed")),
                              ("vendor_confirmation", lambda g: g["free_tier_guard"]["contextdev"].pop("vendor_confirmation")),
                              ("block", lambda g: g["free_tier_guard"].pop("contextdev")), ("free_tier_guard", lambda g: g.pop("free_tier_guard"))):
                for flip in (False, True):
                    base, head = self._cd_docs()
                    if flip: self._flip(head, cfg_too=False)
                    mut(head[C]); r = cd.check(files, base, head)
                    self.assertTrue(any(x.startswith("R26") for x in r), (name, flip, r))
        base, head = self._cd_docs(); self.assertEqual(cd.guard_field_deleted(base, head), [])
        base, head = self._cd_docs(); self._flip(head); self.assertEqual(cd.check([T, C], base, head), [])   # a normal flip still passes
    def test_ac24e_flip_only_allowlist(self):   # AC24e (R13): structural, field-level
        cd = confirmation_diff; T, C = "bench/tos_matrix.yaml", "bench/config.yaml"
        base, head = self._cd_docs(); self._flip(head)
        cyc = "agentwebindex-meta/cycles/2026-10-25-provider-contextdev/"
        self.assertEqual(cd.check([T, C], base, head), [])                                                        # flip only (tos + config)
        b2, h2 = self._cd_docs(); self.assertEqual(cd.check([T], b2, self._flip(h2, cfg_too=False)), [])           # tos record only
        self.assertEqual(cd.check([T, C, cyc + "evidence/vendor-reply.eml", cyc + "summary.md", cyc + "decisions.md", "cycles/x/evidence/r.txt"], base, head), [])
        for extra in ("bench/tests/test_bench.py", "bench/pricing.yaml", "collectors/providers.json", "README.md", "bench/README.md",
                      "bench/awibench/runner.py", ".github/workflows/confirmation-diff.yml", "cycles/x/notes.md",
                      cyc + "evidence/sub/reply.txt", "cycles/x/evidence/a/b.txt",
                      "cycles/README.md", cyc + "README.md", cyc + "notes.md", "cycles/summary.md", "cycles/x/y/summary.md"):   # F12: only cycles/<slug>/{summary,decisions}.md   # M23: evidence is ONE path segment
            r = cd.check([T, C, extra], base, head); self.assertTrue(any("flip-only" in x for x in r), extra); self.assertEqual(len(r), 1 + (extra in cd.PROTECTED), extra)   # protected files also hit SQ2
        self.assertEqual(cd.check([T], base, base), [])                                                           # no change: nothing to check
    def test_ac24e_kitty_probe_cases_all_fail(self):   # AC24e: every Kitty v2 probe, plus the v1 field cases, is rejected
        cd = confirmation_diff; T, C = "bench/tos_matrix.yaml", "bench/config.yaml"
        def case(mut, files=(T, C), flip=True):
            base, head = self._cd_docs()
            if flip: self._flip(head)
            mut(base, head); return cd.check(list(files), base, head)
        tos = lambda d: d["bench/tos_matrix.yaml"]["providers"]; g = lambda d: d["bench/config.yaml"]["free_tier_guard"]["contextdev"]
        probes = {
            "flip + contextdev benchmarking.quote": lambda b, h: tos(h)["contextdev"]["benchmarking"].update(quote="Benchmarking expressly permitted"),
            "flip + nested config date": lambda b, h: g(h)["anchor"].update(date="2026-11-01"),
            "flip + sender in an unrelated tos record": lambda b, h: tos(h)["exa"].update(sender="b"),
            "flip + other tos field (checked_at)": lambda b, h: tos(h)["contextdev"].update(checked_at="2026-10-30"),
            "flip + other config field (stop_at_credits)": lambda b, h: g(h).update(stop_at_credits=990),
            "flip + another provider's record": lambda b, h: tos(h)["keenable"].update(vendor_confirmation={"date": "2026-10-30", "sender": "s", "quote": "q", "evidence_path": "e/p.md", "recorded_pr": "https://github.com/hazeydata/agentwebindex/pull/999"}),
            "flip + keenable benchmarking.quote": lambda b, h: tos(h)["keenable"]["benchmarking"].update(quote="x"),
        }
        for name, mut in probes.items(): self.assertEqual(len(case(mut)), 1, name)
        # record already confirmed at base; a quote-only / date-only / sender-only edit + code is a flip and fails
        for fld in ("quote", "date", "sender"):
            def edit(b, h, fld=fld):
                tos(b)["contextdev"]["vendor_confirmation"] = dict(self.REC); tos(h)["contextdev"]["vendor_confirmation"] = dict(self.REC, **{fld: "2026-10-31" if fld == "date" else "changed"})
            self.assertEqual(len(case(edit, files=(T, "bench/awibench/x.py"), flip=False)), 1, fld)
            self.assertEqual(case(edit, files=(T,), flip=False), [], fld)                                       # the same edit alone is flip-only
        self.assertEqual(len(case(lambda b, h: g(h).update(live_run_allowed=True), files=(C, "bench/awibench/x.py"), flip=False)), 1)   # config flip + code
        self.assertEqual(len(case(lambda b, h: tos(h)["contextdev"].pop("vendor_confirmation"), files=(T, "x.py"), flip=False)), 1)   # removal counts
        # not a flip: a non-confirmation field changed with code is an ordinary PR
        self.assertEqual(case(lambda b, h: tos(h)["contextdev"]["benchmarking"].update(quote="x"), files=(T, "x.py"), flip=False), [])
    def test_ac24h_gate_cannot_be_lowered(self):   # AC24h: any PR; no override exists
        cd = confirmation_diff; T, C = "bench/tos_matrix.yaml", "bench/config.yaml"; files = [T, "bench/awibench/x.py"]
        def run(mut, flip=False):
            base, head = self._cd_docs()
            if flip: self._flip(head)
            mut(head[T]["providers"]); return cd.check(files if not flip else [T, C], base, head)
        lowered = {
            "true -> false": lambda p: p["contextdev"].update(vendor_confirmation_required=False),
            "true -> null": lambda p: p["keenable"].update(vendor_confirmation_required=None),
            "true -> 'true' string": lambda p: p["keenable"].update(vendor_confirmation_required="true"),
            "key deleted": lambda p: p["keenable"].pop("vendor_confirmation_required"),
            "entry deleted": lambda p: p.pop("keenable"),
            "entry renamed": lambda p: p.update(context_dev=p.pop("contextdev")),
        }
        for name, mut in lowered.items():
            r = run(mut); self.assertTrue(any("AC24h" in x for x in r), (name, r))
            if name.startswith("true ->"): self.assertTrue(any("lowered from true" in x for x in r), (name, r))   # named as a lowering, not only as a change   # deleting an entry also removes its confirmation fields (flip rule fires too)
            self.assertTrue(any("AC24h" in x for x in run(mut, flip=True)), name)           # also inside an otherwise valid flip PR
        base, head = self._cd_docs(); head[T] = None
        self.assertTrue(any("AC24h" in x for x in cd.check(files, base, head)))            # unparsable head: fail closed
        # positive controls: unrelated changes pass
        self.assertEqual(run(lambda p: p["contextdev"]["benchmarking"].update(quote="new reading")), [])
        self.assertEqual(run(lambda p: p.update(newco={"allowed": "yes"})), [])               # adding an ungated provider
        # Kitty v4 G10: the rule covers every base entry that HAS the flag, whatever its value (docs: "an entry that has vendor_confirmation_required at the base").
        # Deleting a false/null flag, or the entry holding it, therefore fails; false -> true (raising) and false -> null stay allowed.
        def flagged(v):
            def m(p): p["exa"]["vendor_confirmation_required"] = v
            return m
        for v in (False, None):
            b0, h0 = self._cd_docs(); flagged(v)(b0[T]["providers"]); flagged(v)(h0[T]["providers"])
            h1 = __import__("copy").deepcopy(h0); h1[T]["providers"]["exa"].pop("vendor_confirmation_required")
            self.assertTrue(any("AC24h" in x and "deleted" in x for x in cd.check(files, b0, h1)), v)
            h2 = __import__("copy").deepcopy(h0); h2[T]["providers"].pop("exa")
            self.assertTrue(any("AC24h" in x for x in cd.check(files, b0, h2)), v)
            h3 = __import__("copy").deepcopy(h0); h3[T]["providers"]["exa"]["vendor_confirmation_required"] = True
            self.assertEqual(cd.check(files, b0, h3), [], v)
        self.assertEqual(run(lambda p: p.pop("exa")), [])                                    # deleting an entry WITHOUT the flag is not AC24h
        self.assertEqual(run(lambda p: p.update(newgate={"vendor_confirmation_required": True, "vendor_confirmation": None})), [])   # raising the gate is fine
        self.assertFalse(hasattr(cd, "OVERRIDE") or "override" in (BENCH / "awibench/confirmation_diff.py").read_text().lower().replace("no override", ""))
    def test_ac24e_bootstrap_allowance_closed_defaults_only(self):   # AC24e: the PR that introduces a gated provider (entries start closed) may carry code
        cd = confirmation_diff; T, C = "bench/tos_matrix.yaml", "bench/config.yaml"
        base, head = self._cd_docs(); files = [T, C, "bench/awibench/runner.py"]
        del base[T]["providers"]["contextdev"]; del base[C]["free_tier_guard"]["contextdev"]                         # base: provider absent
        self.assertEqual(cd.check(files, base, head), [])                                                        # new nulls/false, nothing confirmed: allowed
        self.assertEqual(len(cd.check(files, base, self._flip(__import__("copy").deepcopy(head)))), 1)            # new provider arriving confirmed: no allowance
        h2 = __import__("copy").deepcopy(head); h2[T]["providers"]["exa"]["vendor_confirmation"] = dict(self.REC)   # something else confirmed at head
        self.assertEqual(len(cd.check(files, base, h2)), 1)
        h3 = __import__("copy").deepcopy(head); h3[C]["free_tier_guard"]["contextdev"]["live_run_allowed"] = True
        self.assertEqual(len(cd.check(files, base, h3)), 1)                                                      # new field, but not the closed default
        self.assertEqual(len(cd.check(files, base, {T: None, C: head[C]})), 2)                                    # unparsable head: fail closed (flip + AC24h)
        b4 = __import__("copy").deepcopy(base); b4[T]["providers"]["exa"]["vendor_confirmation"] = dict(self.REC)
        h4 = __import__("copy").deepcopy(head); h4[T]["providers"]["exa"]["vendor_confirmation"] = dict(self.REC)
        self.assertEqual(len(cd.check(files, b4, h4)), 1)                                                        # already confirmed elsewhere (unchanged): no allowance
        b5, h5 = self._cd_docs(); b5[C]["free_tier_guard"]["contextdev"]["live_run_allowed"] = True              # existing value changed to false
        self.assertEqual(len(cd.check(files, b5, h5)), 1)                                                        # only NEW fields get the allowance
        # head_confirmed
        self.assertFalse(cd.head_confirmed(head)); self.assertTrue(cd.head_confirmed(h2)); self.assertTrue(cd.head_confirmed(h3))
        self.assertTrue(cd.head_confirmed({T: None, C: {}}))
        # committed repo files: nothing confirmed, values null
        committed = {T: load_yaml(BENCH / "tos_matrix.yaml"), C: load_yaml(BENCH / "config.yaml")}
        self.assertFalse(cd.head_confirmed(committed))
        for pid in ("keenable", "contextdev"): self.assertIsNone(committed[T]["providers"][pid]["vendor_confirmation"])
        self.assertIsNone(committed[C]["free_tier_guard"]["contextdev"]["vendor_confirmation"])
    def test_ac24e_cli_over_git_repo(self):   # AC24e: CLI parses base (merge-base) and HEAD yaml from git, no network
        r = self.tmp / "repo"; (r / "bench/awibench").mkdir(parents=True)
        tos = {"providers": {"contextdev": {"vendor_confirmation_required": True, "vendor_confirmation": None, "benchmarking": {"quote": "q"}}}}
        cfg = {"free_tier_guard": {"contextdev": {"live_run_allowed": False, "vendor_confirmation": None}}}
        w = lambda f, d: (r / f).write_text(json.dumps(d, indent=1) + "\n")
        w("bench/tos_matrix.yaml", tos); w("bench/config.yaml", cfg); (r / "bench/awibench/gates.py").write_text("a=1\n")
        git = lambda *a: subprocess.run(["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a], check=True, capture_output=True, text=True)
        git("init", "-q", "-b", "main"); git("add", "-A"); git("commit", "-qm", "base"); git("checkout", "-qb", "pr")
        tos["providers"]["contextdev"]["vendor_confirmation"] = dict(self.REC); w("bench/tos_matrix.yaml", tos); git("commit", "-qam", "flip")
        with mock.patch.object(confirmation_diff, "Path", lambda _f: type("P", (), {"resolve": lambda s: type("Q", (), {"parents": {2: r}})()})()):
            self.assertEqual(confirmation_diff.main(["main"]), 0)
            tos["providers"]["contextdev"]["benchmarking"]["quote"] = "edited"; w("bench/tos_matrix.yaml", tos); git("commit", "-qam", "quote")
            self.assertEqual(confirmation_diff.main(["main"]), 1)                                                # off-allowlist field
            git("reset", "-q", "--hard", "HEAD~1"); (r / "bench/awibench/gates.py").write_text("a=2\n"); git("commit", "-qam", "code")
            self.assertEqual(confirmation_diff.main(["main"]), 1)                                                # flip + code
            git("reset", "-q", "--hard", "HEAD~1"); sneaky = r / "cycles/s/evidence/a cycles/s/summary.md"           # Kitty v4: a space must not split one nested path into two allowed ones
            sneaky.parent.mkdir(parents=True); sneaky.write_text("x\n"); git("add", "-A"); git("commit", "-qm", "space")
            self.assertEqual(confirmation_diff.main(["main"]), 1)
            git("reset", "-q", "--hard", "HEAD~1"); ok = r / "cycles/s/evidence/vendor reply.txt"                    # a one-segment evidence name with a space stays allowed
            ok.parent.mkdir(parents=True, exist_ok=True); ok.write_text("x\n"); git("add", "-A"); git("commit", "-qm", "ok")
            self.assertEqual(confirmation_diff.main(["main"]), 0)
        for f in ["cycles/s/summary.md\n", "cycles/s/evidence/a\n", "bench/tos_matrix.yaml\n", "cycles/s/decisions.md\nx"]:
            self.assertFalse(confirmation_diff.flip_allowed_path(f), repr(f))                                     # trailing newline is not slack
        self.assertTrue(confirmation_diff.flip_allowed_path("cycles/s/evidence/vendor reply.txt"))
    def test_ac24e_run_tests_propagates_failure(self):   # Kitty v4 F-B: a failing confirmation_diff must make run_tests.sh exit non-zero
        src = (SITE / "run_tests.sh").read_text(); block = src[src.index("# >>> AC24e"):src.index("# <<< AC24e")]
        stub = self.tmp / "stub"; stub.mkdir()
        (stub / "python3").write_text(f"#!/bin/sh\necho \"ARGS:$*\"\nexit 0\n"); (stub / "python3").chmod(0o755)   # Kitty v5 V08: default base is origin/main
        env = {k: v for k, v in os.environ.items() if k != "AWI_CONFIRMATION_BASE"}; env["PATH"] = f"{stub}:{os.environ['PATH']}"
        p = subprocess.run(["bash", "-c", "set -eo pipefail\n" + block], cwd=SITE, capture_output=True, text=True, env=env)
        self.assertIn("ARGS:-m awibench.confirmation_diff origin/main", p.stdout, p.stdout + p.stderr); self.assertIn("confirmation_diff OK vs origin/main", p.stdout)
        for rc in (1, 0):
            (stub / "python3").write_text(f"#!/bin/sh\nexit {rc}\n"); (stub / "python3").chmod(0o755)
            p = subprocess.run(["bash", "-c", "set -eo pipefail\n" + block + "echo REACHED_END\n"], cwd=SITE, capture_output=True, text=True,
                               env={**os.environ, "PATH": f"{stub}:{os.environ['PATH']}", "AWI_CONFIRMATION_BASE": "HEAD"})
            if rc: self.assertNotEqual(p.returncode, 0, p.stdout); self.assertNotIn("REACHED_END", p.stdout); self.assertIn("FAILED: confirmation_diff", p.stdout)
            else: self.assertEqual(p.returncode, 0, p.stdout + p.stderr); self.assertIn("confirmation_diff OK", p.stdout)
    def test_r21_run_tests_warns_when_base_is_head(self):   # R21 (2): local override warns when base == head; CI never references it
        src = (SITE / "run_tests.sh").read_text(); block = src[src.index("# >>> AC24e"):src.index("# <<< AC24e")]
        stub = self.tmp / "stub21"; stub.mkdir(); (stub / "python3").write_text("#!/bin/sh\nexit 0\n"); (stub / "python3").chmod(0o755)
        r = self.tmp / "r21"; (r / "bench").mkdir(parents=True)
        git = lambda *a: subprocess.run(["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a], check=True, capture_output=True, text=True)
        git("init", "-q", "-b", "main"); (r / "a").write_text("1\n"); git("add", "-A"); git("commit", "-qm", "one"); (r / "a").write_text("2\n"); git("commit", "-qam", "two")
        run = lambda base: subprocess.run(["bash", "-c", "set -eo pipefail\n" + block], cwd=r / "bench", capture_output=True, text=True,
                                          env={**os.environ, "PATH": f"{stub}:{os.environ['PATH']}", "AWI_CONFIRMATION_BASE": base})
        for base in ("HEAD", "main", git("rev-parse", "HEAD").stdout.strip()):
            p = run(base); self.assertEqual(p.returncode, 0, p.stdout + p.stderr); self.assertIn("WARNING: confirmation_diff base", p.stdout, base); self.assertIn("same commit as HEAD", p.stdout)
        p = run("HEAD~1"); self.assertEqual(p.returncode, 0, p.stdout + p.stderr); self.assertNotIn("WARNING", p.stdout)                # positive control: a real base, no warning
        for wf in sorted((SITE / ".github/workflows").glob("*.yml")): self.assertNotIn("AWI_CONFIRMATION_BASE", wf.read_text(), wf.name)
    def test_r21_workflows_take_shas_from_event(self):   # R21 (3): both confirmation workflows wire base and head SHAs from the event, not env, inputs or branch names
        ev = {"AWI_BASE_REF": "${{ github.event.pull_request.base.sha }}", "AWI_HEAD_REF": "${{ github.event.pull_request.head.sha }}"}
        for name, trig in (("confirmation-diff-base.yml", "pull_request_target"), ("confirmation-diff.yml", "pull_request")):
            text = (SITE / ".github/workflows" / name).read_text(); doc = parse_workflow_yaml(text)
            self.assertEqual(list(doc["on"]), [trig], name)                                                # no workflow_dispatch / inputs
            self.assertNotRegex(text, r"inputs\.|github\.base_ref|github\.head_ref|origin/|vars\.|env\.AWI", name)
            steps = [st for job in doc["jobs"].values() for st in job["steps"] if "awibench.confirmation_diff" in st.get("run", "")]
            self.assertEqual(len(steps), 1, name); st = steps[0]
            self.assertEqual({k: st["env"][k] for k in ev}, ev, name)
            self.assertRegex(st["run"], r'awibench\.confirmation_diff "\$\{?(base|AWI_BASE_REF)\}?" "\$\{?(head|AWI_HEAD_REF)\}?"', name)
            if "base=" in st["run"]: self.assertIn('base="$AWI_BASE_REF"; head="$AWI_HEAD_REF"', st["run"], name)
    def test_r16_sq1_bootstrap_fails_closed(self):   # SQ1 + R17 SQ-A: base without a checker FAILS, no exemption (fake/empty/sys.exit(0) checkers too)
        wf = (SITE / ".github/workflows/confirmation-diff-base.yml").read_text()
        block = "\n".join(l.strip() for l in wf[wf.index("# >>> base-judge"):wf.index("# <<< base-judge")].splitlines())
        r = self.tmp / "sq1"; (r / "bench").mkdir(parents=True); (r / "bench/tos_matrix.yaml").write_text("{}\n"); (r / "bench/config.yaml").write_text("{}\n")
        git = lambda *a: subprocess.run(["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a], check=True, capture_output=True, text=True)
        git("init", "-q", "-b", "main"); git("add", "-A"); git("commit", "-qm", "base: no checker")
        judge = lambda head: subprocess.run(["bash", "-c", block], cwd=r, capture_output=True, text=True, env={**os.environ, "AWI_BASE_REF": "main", "AWI_HEAD_REF": head})
        real = (BENCH / "awibench/confirmation_diff.py").read_text()
        for name, body in (("other", None), ("real", real), ("fake", "print('OK')\n"), ("empty", ""), ("exit0", "import sys\nsys.exit(0)\n")):
            git("checkout", "-q", "main"); git("checkout", "-qb", name); (r / "README.md").write_text(name + "\n")
            if body is not None: (r / "bench/awibench").mkdir(exist_ok=True); (r / "bench/awibench/confirmation_diff.py").write_text(body)
            git("add", "-A"); git("commit", "-qm", name); git("checkout", "-q", "main")
            p = judge(name); self.assertEqual(p.returncode, 1, name + p.stdout); self.assertIn("SQ1: fail closed, no exemption", p.stdout, name)
            self.assertNotIn("BOOTSTRAP", p.stdout)
        self.assertNotIn("BOOTSTRAP", block); self.assertNotRegex(block, r"PR_NUMBER|pull_request\.number|grep -q")
        r2 = self.tmp / "sq1b"; (r2 / "bench/awibench").mkdir(parents=True)                                                       # positive control: base WITH a checker judges normally
        for f in ("confirmation_diff.py", "gates.py"): shutil.copy(BENCH / "awibench" / f, r2 / "bench/awibench" / f)
        (r2 / "bench/tos_matrix.yaml").write_text("{}\n"); (r2 / "bench/config.yaml").write_text("{}\n")
        git2 = lambda *a: subprocess.run(["git", "-C", str(r2), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a], check=True, capture_output=True, text=True)
        git2("init", "-q", "-b", "main"); git2("add", "-A"); git2("commit", "-qm", "base"); git2("checkout", "-qb", "doc"); (r2 / "README.md").write_text("x\n")
        git2("add", "-A"); git2("commit", "-qm", "doc"); git2("checkout", "-q", "main")
        p = subprocess.run(["bash", "-c", block], cwd=r2, capture_output=True, text=True, env={**os.environ, "AWI_BASE_REF": "main", "AWI_HEAD_REF": "doc"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr); self.assertIn("judging doc with the base checker", p.stdout)
    def test_q04_diff_from_merge_base_not_base_tip(self):   # Kitty v6 Q04: a base branch that moved ahead must not count against the PR
        cd = confirmation_diff; r = self.tmp / "mb"; (r / "bench/awibench").mkdir(parents=True)
        for f in ("confirmation_diff.py", "gates.py"): shutil.copy(BENCH / "awibench" / f, r / "bench/awibench" / f)
        (r / "bench/awibench/__init__.py").write_text("")
        tos = {"providers": {"contextdev": {"vendor_confirmation_required": True, "vendor_confirmation": None}}}
        (r / "bench/tos_matrix.yaml").write_text(json.dumps(tos) + "\n"); (r / "bench/config.yaml").write_text("{}\n")
        git = lambda *a: subprocess.run(["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a], check=True, capture_output=True, text=True)
        git("init", "-q", "-b", "main"); git("add", "-A"); git("commit", "-qm", "base"); git("checkout", "-qb", "pr")
        tos["providers"]["contextdev"]["vendor_confirmation"] = dict(self.REC); (r / "bench/tos_matrix.yaml").write_text(json.dumps(tos) + "\n"); git("commit", "-qam", "flip only")
        git("checkout", "-q", "main"); (r / "README.md").write_text("main moved\n"); (r / "bench/runner.py").write_text("x=1\n")
        moved = {"providers": {**json.loads(json.dumps(tos))["providers"], "exa": {"allowed": "yes"}}}; moved["providers"]["contextdev"]["vendor_confirmation"] = None
        (r / "bench/tos_matrix.yaml").write_text(json.dumps(moved) + "\n")                                           # main also moved the yaml (Q04s: docs read at the merge-base)
        with (r / "bench/awibench/gates.py").open("a") as fh: fh.write("# main moved\n")
        git("add", "-A"); git("commit", "-qm", "main moves ahead (code + protected file)"); git("checkout", "-q", "pr")
        with mock.patch.object(cd, "Path", lambda _f: type("P", (), {"resolve": lambda s: type("Q", (), {"parents": {2: r}})()})()):
            self.assertEqual(cd.main(["main"]), 0)                                                                # judged from merge-base: flip-only
            git("checkout", "-q", "main"); git("checkout", "-qb", "bad"); (r / "bench/runner.py").write_text("x=2\n")
            tos["providers"]["contextdev"]["vendor_confirmation"] = dict(self.REC); (r / "bench/tos_matrix.yaml").write_text(json.dumps(tos) + "\n")
            git("commit", "-qam", "flip + code"); self.assertEqual(cd.main(["main"]), 1)                                   # control
    def test_s09_type_check_sets_and_dates(self):   # Kitty v6 S09/T01: yaml !!set and dates must not equal their repr strings
        import datetime
        cd = confirmation_diff
        for a, b in (({"a"}, "{'a'}"), (datetime.date(2026, 10, 30), "datetime.date(2026, 10, 30)"), (datetime.date(2026, 10, 30), "2026-10-30")):
            self.assertFalse(cd.same(a, b), (a, b)); self.assertFalse(cd.same(b, a)); self.assertTrue(cd.same(a, a))
        T = "bench/tos_matrix.yaml"
        for old, new in (("{'a'}", {"a"}), ("datetime.date(2026, 10, 30)", datetime.date(2026, 10, 30))):
            b, h = self._cd_docs(); b[T]["providers"]["contextdev"]["benchmarking"]["quote"] = old; h[T]["providers"]["contextdev"]["benchmarking"]["quote"] = new
            self.assertTrue(cd.changed_paths(b[T], h[T]), (old, new))
            self._flip(h); self.assertTrue(any("flip-only" in x for x in cd.check([T, "bench/config.yaml"], b, h)), (old, new))
    def test_r19_one_record_validator(self):   # R19: one validator in gates.py; vendor_confirmed() and the CI schema check both use it
        import datetime
        from awibench import gates
        cd = confirmation_diff; T, C = "bench/tos_matrix.yaml", "bench/config.yaml"; R = dict(self.REC)
        def all_paths(rec):                                                                                               # validator, vendor_confirmed, check() (non-flip and flip)
            b, h = self._cd_docs(); h[T]["providers"]["keenable"]["vendor_confirmation"] = rec
            b2, h2 = self._cd_docs(); h2[C]["free_tier_guard"]["contextdev"]["vendor_confirmation"] = rec
            b3, h3 = self._cd_docs(); self._flip(h3); h3[T]["providers"]["contextdev"]["vendor_confirmation"] = rec
            return (gates.record_error(rec), gates.vendor_confirmed({"vendor_confirmation_required": True, "vendor_confirmation": rec}),
                    [x for x in cd.check(["README.md"], b, h) if "SQ-C" in x], [x for x in cd.check([C], b2, h2) if "SQ-C" in x],
                    [x for x in cd.check([T, C], b3, h3) if "SQ-C" in x])
        good = [R, dict(R, date="2024-02-29"),
                dict(R, recorded_pr="https://github.com/hazeydata/agentwebindex-meta/pull/1")]
        for rec in good:                                                                                                  # positive controls
            err, ok, *sqc = all_paths(rec); self.assertIsNone(err, rec); self.assertTrue(ok, rec); self.assertEqual(sqc, [[], [], []], rec)
        bad = {
            "extra key": dict(R, note="x"), "missing recorded_pr": {k: v for k, v in R.items() if k != "recorded_pr"},
            "missing quote key": {k: v for k, v in R.items() if k != "quote"}, "non-str key": dict(R, **{}) | {1: "x"},
            "unquoted yaml date": dict(R, date=datetime.date(2026, 10, 30)), "invalid date": dict(R, date="2026-02-30"),
            "bad date format": dict(R, date="2026-10-3"), "basic iso date": dict(R, date="20261030"), "week date": dict(R, date="2026-W44-5"), "date with time": dict(R, date="2026-10-30T00:00"), "date int": dict(R, date=20261030),
            "empty sender": dict(R, sender=" "), "sender int": dict(R, sender=7), "sender set (K8-02n)": dict(R, sender={"a"}),
            "http url": dict(R, recorded_pr="http://github.com/x/pull/1"), "int PR (Z02)": dict(R, recorded_pr=27), "bare https": dict(R, recorded_pr="https://"),
            "PR not url": dict(R, recorded_pr="#27"), "PR with space": dict(R, recorded_pr="https://x y"),
            "both null": dict(R, quote=None, evidence_path=None), "both empty": dict(R, quote="", evidence_path=" "),
            "quote null (R26)": dict(R, quote=None), "evidence_path null (R26)": dict(R, evidence_path=None), "quote empty (R26)": dict(R, quote=""), "evidence_path empty (R26)": dict(R, evidence_path=" "),
            "quote list": dict(R, quote=["q"]), "quote int, path ok": dict(R, quote=1), "evidence_path set (K8-02n)": dict(R, evidence_path={"p"}),
            "quote false": dict(R, quote=False), "link string": "https://example.invalid/reply", "empty mapping": {}, "list": [R],
        }
        for name, rec in bad.items():
            err, ok, *sqc = all_paths(rec)
            self.assertTrue(err, name); self.assertFalse(ok, name)
            for hits in sqc: self.assertTrue(hits, (name, sqc))
        self.assertIn("real calendar date", gates.record_error(bad["invalid date"]))
        self.assertIn("https://", gates.record_error(bad["int PR (Z02)"])); self.assertIn("extra", gates.record_error(bad["extra key"]))
        self.assertIn("quoted", gates.record_error(bad["unquoted yaml date"]))
        with mock.patch.object(gates, "record_error", return_value="sentinel") as m:                                      # one implementation: both callers go through it
            self.assertFalse(gates.vendor_confirmed({"vendor_confirmation_required": True, "vendor_confirmation": R}))
            self.assertEqual(cd.record_shape_error(R), "sentinel"); self.assertEqual(m.call_count, 2)
        src = (BENCH / "awibench/confirmation_diff.py").read_text()
        self.assertNotRegex(src, r"recorded_pr\"|\"sender\"|RECORD_|https://\"")                                         # no second copy of the rules
        self.assertEqual(gates.RECORD_KEYS, ("date", "sender", "quote", "evidence_path", "recorded_pr"))
    def test_r17_sqc_schema_types_every_pr(self):   # R17 SQ-C + R18: types and record shape on every PR, tested through check()
        import datetime
        cd = confirmation_diff; T, C = "bench/tos_matrix.yaml", "bench/config.yaml"
        def sqc(edit, files=("README.md",)):                                                                              # a non-flip PR (K7-11): check() must still report SQ-C
            b, h = self._cd_docs(); edit(h); r = cd.check(list(files), b, h); return [x for x in r if "SQ-C" in x], r
        hits, r = sqc(lambda h: None); self.assertEqual(r, [])                                                             # positive control: valid types, nothing confirmed
        hits, r = sqc(lambda h: self._flip(h), files=(T, C)); self.assertEqual(r, [])                                      # positive control: full valid record (flip-only PR)
        for k in ("quote", "evidence_path"):                                                                               # R26: both required
            rec = dict(self.REC, **{k: None}); hits, r = sqc(lambda h: self._flip(h) and h[T]["providers"]["contextdev"].update(vendor_confirmation=rec), files=(T, C))
            self.assertTrue(hits, k)
        hits, r = sqc(lambda h: h[C]["free_tier_guard"]["contextdev"].update(live_run_allowed=0), files=(C,))
        self.assertTrue(any("live_run_allowed must be bool" in x for x in hits), r)                                       # data-only false -> 0
        for bad in ("true", 1, None):
            hits, r = sqc(lambda h: h[T]["providers"]["keenable"].update(vendor_confirmation_required=bad))
            self.assertTrue(any("vendor_confirmation_required must be bool" in x for x in hits), (bad, r))
        partial = [{k: v for k, v in self.REC.items() if k != drop} for drop in ("date", "sender", "recorded_pr")]
        partial += [{k: v for k, v in self.REC.items() if k not in ("quote", "evidence_path")}, {"evidence_path": "https://example.invalid/reply"},
                    dict(self.REC, date=""), dict(self.REC, sender=None), dict(self.REC, date=datetime.date(2026, 10, 30))]
        bads = ["yes", "https://example.invalid/reply", "", 0, False, True, {}, [], ["x"], {"a"}, frozenset({"date"}), 1.0] + partial  # K7-02 set, K7-04 falsy, link, partial
        for where in ("tos", "cfg"):
            for v in bads:
                def edit(h, v=v):
                    (h[T]["providers"]["keenable"] if where == "tos" else h[C]["free_tier_guard"]["contextdev"])["vendor_confirmation"] = v
                hits, r = sqc(edit); self.assertTrue(any("vendor_confirmation" in x for x in hits), (where, v, r))
                hits, r = sqc(edit, files=(T, C)); self.assertTrue(hits, (where, v, r))
        self.assertIsNone(cd.record_shape_error(None)); self.assertIsNone(cd.record_shape_error(dict(self.REC)))
        live = {f: cd.parse((SITE / f).read_text()) for f in cd.DATA_FILES}; self.assertEqual(cd.schema_errors(live), [])   # the committed data passes
    def test_r16_sq2_protected_files(self):   # SQ2: the base checker fails any diff touching gates.py, confirmation_diff.py or the workflows
        cd = confirmation_diff; base, head = self._cd_docs()
        for f in cd.PROTECTED:
            r = cd.check([f], base, head); self.assertTrue(any(x.startswith("SQ2") and f in x for x in r), f)
            self.assertTrue(any(x.startswith("SQ2") for x in cd.check([f], base, head, base_has=list(cd.PROTECTED))), f)
            self.assertEqual(cd.check([f], base, head, base_has=[]), [], f)                                               # absent at base: SQ2 n/a (with no base checker, SQ1 fails closed)
        self.assertEqual(cd.check(["bench/awibench/runner.py", "README.md"], base, head), [])                                 # positive control
        r = self.tmp / "sq2"; (r / "bench/awibench").mkdir(parents=True)                                                      # CLI: base_has comes from git at the merge-base
        for f in ("confirmation_diff.py", "gates.py"): shutil.copy(BENCH / "awibench" / f, r / "bench/awibench" / f)
        (r / "bench/awibench/__init__.py").write_text(""); (r / "bench/tos_matrix.yaml").write_text("{}\n"); (r / "bench/config.yaml").write_text("{}\n")
        git = lambda *a: subprocess.run(["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a], check=True, capture_output=True, text=True)
        git("init", "-q", "-b", "main"); git("add", "-A"); git("commit", "-qm", "base"); git("checkout", "-qb", "pr")
        with (r / "bench/awibench/gates.py").open("a") as fh: fh.write("# harmless-looking edit\n")
        git("commit", "-qam", "edit gates.py only")
        with mock.patch.object(cd, "Path", lambda _f: type("P", (), {"resolve": lambda s: type("Q", (), {"parents": {2: r}})()})()):
            self.assertEqual(cd.main(["main"]), 1)
            git("checkout", "-q", "main"); git("checkout", "-qb", "ok"); (r / "README.md").write_text("x\n"); git("add", "-A"); git("commit", "-qm", "docs")
            self.assertEqual(cd.main(["main"]), 0)                                                                        # positive control over git
        self.assertEqual(set(cd.PROTECTED), {"bench/awibench/gates.py", "bench/awibench/confirmation_diff.py",
                                              ".github/workflows/confirmation-diff.yml", ".github/workflows/confirmation-diff-base.yml",
                                              ".github/workflows/benchmark.yml"})   # Keenable R5 (1)
        self.assertNotIn("run_tests.sh", cd.PROTECTED)
    def test_r16_sq3_strict_type_and_value(self):   # SQ3: false->0/'false'/null and true->1/'true' are changes
        cd = confirmation_diff; T, C = "bench/tos_matrix.yaml", "bench/config.yaml"; files = [T, C, "bench/awibench/runner.py"]
        for old, news in ((False, (0, "false", None, 0.0)), (True, (1, "true", None, 1.0))):
            for new in news:
                self.assertFalse(cd.same(old, new), (old, new)); self.assertTrue(cd.same(old, old))
                b, h = self._cd_docs(); b[T]["providers"]["exa"]["vendor_confirmation_required"] = old; h[T]["providers"]["exa"]["vendor_confirmation_required"] = new
                self.assertTrue(any("AC24h" in x for x in cd.check(files, b, h)), (old, new))                               # the flag
                b, h = self._cd_docs(); b[C]["free_tier_guard"]["contextdev"]["live_run_allowed"] = old; h[C]["free_tier_guard"]["contextdev"]["live_run_allowed"] = new
                self.assertTrue(cd.changed_paths(b[C], h[C]), (old, new))
                self.assertTrue(any("flip-only" in x for x in cd.check(files, b, h)), (old, new))                          # a confirmation field + code
        b, h = self._cd_docs(); b[T]["providers"]["exa"]["vendor_confirmation_required"] = False; h[T]["providers"]["exa"]["vendor_confirmation_required"] = True
        self.assertEqual(cd.check(files, b, h), [])                                                                        # positive control: raising false -> true
        b, h = self._cd_docs(); self.assertEqual(cd.check(files, b, h), [])                                                 # identical values: no change
        b, h = self._cd_docs(); del b[T]["providers"]["contextdev"]; del b[C]["free_tier_guard"]["contextdev"]
        h[C]["free_tier_guard"]["contextdev"]["live_run_allowed"] = 0
        self.assertTrue(cd.check(files, b, h))                                                                             # bootstrap allowance is strict: 0 is not false
    def test_ac24h_base_judged_ci_r15(self):   # R15: the BASE checker judges the PR; a PR that rewrites the checker, the flag name and the key still fails
        wf = (SITE / ".github/workflows/confirmation-diff-base.yml").read_text()
        self.assertRegex(wf, r"(?m)^on:\n  pull_request_target:"); self.assertIn("ref: \"${{ github.event.pull_request.base.sha }}\"", wf)
        self.assertIn("persist-credentials: false", wf); self.assertEqual(wf.count("pull_request.head.sha"), 1); self.assertIn('AWI_HEAD_REF: "${{ github.event.pull_request.head.sha }}"', wf); self.assertNotIn("head.ref", wf)   # PR head is data (a SHA), never checked out
        block = "\n".join(l.strip() for l in wf[wf.index("# >>> base-judge"):wf.index("# <<< base-judge")].splitlines())
        r = self.tmp / "bj"; (r / "bench/awibench").mkdir(parents=True)
        for f in ("confirmation_diff.py", "gates.py"): shutil.copy(BENCH / "awibench" / f, r / "bench/awibench" / f)
        (r / "bench/awibench/__init__.py").write_text("")
        tos = {"providers": {"contextdev": {"vendor_confirmation_required": True, "vendor_confirmation": None}, "exa": {"allowed": "yes"}}}
        w = lambda d: (r / "bench/tos_matrix.yaml").write_text(json.dumps(d, indent=1) + "\n")
        (r / "bench/config.yaml").write_text("{}\n"); w(tos)
        git = lambda *a: subprocess.run(["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a], check=True, capture_output=True, text=True)
        git("init", "-q", "-b", "main"); git("add", "-A"); git("commit", "-qm", "base")
        judge = lambda head: subprocess.run(["bash", "-c", block], cwd=r, capture_output=True, text=True, env={**os.environ, "AWI_BASE_REF": "main", "AWI_HEAD_REF": head})
        git("checkout", "-qb", "ok"); tos["providers"]["exa"]["allowed"] = "no"; w(tos); git("commit", "-qam", "unrelated")
        p = judge("ok"); self.assertEqual(p.returncode, 0, p.stdout + p.stderr); self.assertIn("flag name from base gates.py: vendor_confirmation_required", p.stdout)   # positive control
        git("checkout", "-q", "main"); git("checkout", "-qb", "attack")                                           # combined attack, all in one PR:
        g = r / "bench/awibench/gates.py"; g.write_text(g.read_text().replace("vendor_confirmation_required", "vendor_confirmation_requirement"))   # 1. rename REQUIRED_FLAG
        c = r / "bench/awibench/confirmation_diff.py"; c.write_text(c.read_text().replace("    return 1 if probs else 0", "    return 0"))     # 2. edit the checker
        tos["providers"]["exa"]["allowed"] = "yes"; cd_e = tos["providers"]["contextdev"]; del cd_e["vendor_confirmation_required"]
        cd_e["vendor_confirmation_requirement"] = False; w(tos); git("commit", "-qam", "attack")                    # 3. rename the key, set to false
        own = subprocess.run([sys.executable, "-m", "awibench.confirmation_diff", "main"], cwd=r / "bench", capture_output=True, text=True)
        self.assertEqual(own.returncode, 0, own.stdout)                                                            # the PR's own checker would pass it...
        git("checkout", "-q", "main")                                                                              # CI has only the base checked out
        p = judge("attack"); self.assertNotEqual(p.returncode, 0, p.stdout); self.assertIn("AC24h", p.stdout)        # ...the base-judged path fails it (head read as data)
        self.assertEqual(git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "main")                         # judging never checked anything out
        p = judge("ok"); self.assertEqual(p.returncode, 0, p.stdout + p.stderr)                                   # and the clean PR still passes from a base checkout
    def test_ac24e_move_into_evidence_fails_both_checkers(self):   # Kitty v5: `git mv X cycles/s/evidence/X` in a flip PR must fail (old path counts)
        wf = (SITE / ".github/workflows/confirmation-diff-base.yml").read_text()
        block = "\n".join(l.strip() for l in wf[wf.index("# >>> base-judge"):wf.index("# <<< base-judge")].splitlines())
        for src in ("bench/tests/test_bench.py", "run_tests.sh", ".github/workflows/confirmation-diff.yml"):
            r = self.tmp / ("mv-" + src.replace("/", "_")); (r / "bench/awibench").mkdir(parents=True)
            for f in ("confirmation_diff.py", "gates.py"): shutil.copy(BENCH / "awibench" / f, r / "bench/awibench" / f)
            (r / "bench/awibench/__init__.py").write_text("")
            tos = {"providers": {"contextdev": {"vendor_confirmation_required": True, "vendor_confirmation": None}}}
            w = lambda d: (r / "bench/tos_matrix.yaml").write_text(json.dumps(d, indent=1) + "\n")
            w(tos); (r / "bench/config.yaml").write_text("{}\n")
            (r / src).parent.mkdir(parents=True, exist_ok=True); shutil.copy(SITE / src, r / src)   # real content, so git would see a 100% rename
            git = lambda *a: subprocess.run(["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a], check=True, capture_output=True, text=True)
            git("init", "-q", "-b", "main"); git("add", "-A"); git("commit", "-qm", "base"); git("checkout", "-qb", "pr")
            tos["providers"]["contextdev"]["vendor_confirmation"] = dict(self.REC); w(tos)
            (r / "cycles/s/evidence").mkdir(parents=True); git("mv", src, "cycles/s/evidence/" + Path(src).name); git("add", "-A"); git("commit", "-qm", "flip + move")
            self.assertIn("R", git("diff", "--name-status", "-M", "main..pr").stdout)                              # git itself sees a rename here
            with mock.patch.object(confirmation_diff, "Path", lambda _f: type("P", (), {"resolve": lambda s: type("Q", (), {"parents": {2: r}})()})()):
                self.assertEqual(confirmation_diff.main(["main"]), 1, src)                                         # PR checker
            git("checkout", "-q", "main")
            p = subprocess.run(["bash", "-c", block], cwd=r, capture_output=True, text=True, env={**os.environ, "AWI_BASE_REF": "main", "AWI_HEAD_REF": "pr"})
            self.assertNotEqual(p.returncode, 0, src + p.stdout); self.assertIn(src, p.stdout)                       # base-judged path names the old path
    def test_ac24e_ci_wiring(self):   # AC24e CI half: run_tests.sh and a pull_request workflow both run the checker
        self.assertIn("python3 -m awibench.confirmation_diff", (SITE / "run_tests.sh").read_text())
        wf = (SITE / ".github/workflows/confirmation-diff.yml").read_text()   # text checks: workflow YAML is not JSON-style, PyYAML is optional
        self.assertRegex(wf, r"(?m)^on:\n  pull_request:"); self.assertIn("fetch-depth: 0", wf)
        self.assertIn('python -m awibench.confirmation_diff "$AWI_BASE_REF" "$AWI_HEAD_REF"', wf); self.assertNotIn("base_ref", wf)
    def test_ac11_creditguard_allows_is_the_stop_check(self):   # AC11 (kills mutant K1: allows <= -> <)
        d = self.tmp / "cg"; d.mkdir(); g = guard_mod.CreditGuard(d, "contextdev", None, 900)
        try:
            g.record(899, 0)
            self.assertTrue(g.allows(1)); self.assertFalse(g.allows(2))              # 899 + 1 = 900 runs, 901 stops
            self.assertTrue(g.allows(0, meter=900)); self.assertFalse(g.allows(1, meter=900))   # meter above the counter wins
            self.assertFalse(g.allows(1, meter=None) and g.allows(1, meter=950))
        finally: g.release()
        src = (BENCH / "awibench/runner.py").read_text()
        self.assertIn("cg.allows(est, mv)", src); self.assertNotIn('eff + est > fg["stop_at_credits"]', src)   # runner has no second copy of the check
    def test_ac24f_keenable_no_adapter_zero_calls(self):   # AC24f
        sys.path.insert(0, str(SITE / "collectors")); import benchmark as B
        self.assertNotIn("keenable", B.ADAPTERS)
        prov = {"id": "keenable", "name": "Keenable", "categories": ["search"], "key_env": "KEENABLE_API_KEY"}
        self.assertNotIn("keenable", [p["id"] for p in json.loads((SITE / "collectors/providers.json").read_text())["providers"]])
        for fn in ("pricing.yaml", "config.yaml"): self.assertNotIn("keenable", (BENCH / fn).read_text().lower())
        self.assertEqual(B.keyed_providers([prov], {"KEENABLE_API_KEY": "dummy-not-a-key"}), [])
        adapters = {"keenable": {"search": self.ad_search}}   # even a (synthetic) adapter must not be reached
        pricing = self.pricing | {"keenable": {"search": mocks.pricing_fixture("credits")}}
        for cfg in (mocks.mock_cfg(CFG), self.cfg):
            s = self.go(providers=[prov], adapters=adapters, pricing=pricing, cfg=cfg, env={"KEENABLE_API_KEY": "dummy-not-a-key"})
            self.assertEqual((self.prov(s, "keenable")["status"], self.calls), ("skipped_no_vendor_confirmation", []))
        self.assertFalse(guard_mod.live_allowed({"free_tier_guard": {"keenable": {"live_run_allowed": True, "vendor_confirmation": "x"}}}, self.tos, "keenable"))
        self.assertFalse((self.tmp / "runs").exists())


class SnapshotPricesGate(unittest.TestCase):
    def test_r10_price_snapshot_skips_unconfirmed_providers(self):  # AC24 / R10: no context.dev page fetch, no data/prices/contextdev.json
        sys.path.insert(0, str(SITE / "collectors")); import snapshot_prices as S
        provs = json.loads((SITE / "collectors/providers.json").read_text())["providers"]
        tos = load_yaml(BENCH / "tos_matrix.yaml")["providers"]
        ids = [p["id"] for p in S.snapshot_providers(provs, tos)]
        self.assertNotIn("contextdev", ids)
        self.assertEqual(len(ids), 15)
    def test_ac24g_snapshot_skip_follows_vendor_confirmed(self):   # AC24g (R12): skip iff gates.vendor_confirmed is false; main() uses it
        sys.path.insert(0, str(SITE / "collectors")); import snapshot_prices as S
        provs = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        tos = {"a": {"vendor_confirmation_required": True, "vendor_confirmation": None},
               "b": {"vendor_confirmation_required": True, "vendor_confirmation": {"date": "2026-11-01", "sender": "s", "quote": "q", "evidence_path": "e/p.md", "recorded_pr": "https://github.com/hazeydata/agentwebindex/pull/999"}}}
        self.assertEqual([p["id"] for p in S.snapshot_providers(provs, tos)], ["b", "c"])   # unflagged c unaffected
        self.assertIn("provs=snapshot_providers(", (SITE / "collectors/snapshot_prices.py").read_text())
        self.assertIn("snapshot_prices", (BENCH / "README.md").read_text())   # documented (docs match code)

class NoVendorMailPath(unittest.TestCase):
    def test_ac27a_no_mail_send_path(self):   # AC27a (R12): no code path sends the vendor email
        pat = re.compile(r"smtplib|sendmail|send_mail|SMTP\(|mailto:|hello@context\.dev|gmail|sendgrid|mailgun|postmark|/messages/send", re.I)
        hits = []
        for f in list(SITE.glob("bench/awibench/*.py")) + list(SITE.glob("collectors/*.py")) + list(SITE.glob(".github/workflows/*.yml")) + list(SITE.glob("*.sh")):
            hits += [f"{f.name}:{i}" for i, l in enumerate(f.read_text(errors="replace").splitlines(), 1) if pat.search(l)]
        self.assertEqual(hits, [])
        # positive control: the same pattern catches each mail-send form it is meant to catch
        for bad in ("import smtplib", "os.system('sendmail x')", "send_mail(to)", "smtplib.SMTP('h')", "href='mailto:x'", "to='hello@context.dev'",
                    "gmail.users().messages().send()", "import sendgrid", "requests.post(mailgun)", "postmark.send()", "POST /messages/send"):
            self.assertRegex(bad, pat, bad)
        self.assertNotRegex("vendor_confirmation_required = True", pat)
