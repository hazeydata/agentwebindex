"""Issue #60: snippet parity (300 chars, every provider) and one URL equivalence rule. Offline: urllib is never reached."""
import contextlib, io, json, subprocess, sys, tempfile
from pathlib import Path
from unittest import mock
import pytest
BENCH = Path(__file__).resolve().parent.parent; SITE = BENCH.parent
sys.path.insert(0, str(BENCH)); sys.path.insert(0, str(SITE / "collectors")); sys.path.insert(0, str(BENCH / "tools"))
from awibench import checks, urlmatch, redirects, util
from awibench.util import read_jsonl, snippet_text, SNIPPET_MAX_CHARS, norm_url
import benchmark as B

LONG = "word " * 1000

# ---- A: snippets
def test_snippet_text():
    assert SNIPPET_MAX_CHARS == 300
    assert snippet_text(None) is None and snippet_text("") is None and snippet_text("  \n ") is None and snippet_text([]) is None
    assert snippet_text(["a  b", "", None, "c\n\td"]) == "a b c d"
    assert len(snippet_text(LONG)) <= 300

def test_exa_requests_highlights_and_joins():
    seen = []
    def post(url, headers, body): seen.append(body); return 200, {"results": [{"url": "https://a/", "title": "t", "highlights": ["x" * 200, "y" * 200]},
                                                                             {"url": "https://b/", "highlights": []}, {"url": "https://c/"}]}
    with mock.patch.object(B, "_post", post): out = B._exa_search("k", {"query": "q"})
    assert seen[0]["contents"]["highlights"] is True and seen[0]["numResults"] == 10 and out["billed_units"] == 1
    assert out["results"][0]["snippet"] == ("x" * 200 + " " + "y" * 200)[:300]
    assert out["results"][1]["snippet"] is None and out["results"][2]["snippet"] is None

def test_each_adapter_caps_snippet():
    item = {"query": "q", "id": "se-x"}
    with mock.patch.object(B, "_post", lambda *a: (200, {"results": [{"url": "u", "content": LONG}]})):
        assert len(B._tavily_search("k", item)["results"][0]["snippet"]) <= 300
    with mock.patch.object(B, "_post", lambda *a: (200, {"data": {"web": [{"url": "u", "description": LONG}]}})):
        assert len(B._firecrawl_search("k", item)["results"][0]["snippet"]) <= 300
    with mock.patch.object(B, "_get_json", lambda *a, **k: (200, [{"searchResult": {"url": "u", "rank": 1, "description": LONG}, "metadata": {"url": "https://a/"}}])):
        assert len(B._apify_search("k", item)["results"][0]["snippet"]) <= 300
    with mock.patch.object(B, "_get_json", lambda *a, **k: (200, [{"searchResult": {"url": "u", "rank": 1, "description": ""}, "metadata": {"url": "https://a/", "description": LONG}}])):
        assert snippet_text(LONG) == B._apify_search("k", item)["results"][0]["snippet"]
    with mock.patch.object(B, "_contextdev_post", lambda *a: (200, {"results": [{"url": "u", "snippet": LONG}]})):
        assert len(B._contextdev_search("k", item)["results"][0]["snippet"]) <= 300
    class R:
        status = 200
        def read(self): return json.dumps({"results": [{"url": "u", "snippet": LONG}]}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False
    with mock.patch("urllib.request.urlopen", lambda *a, **k: R()):
        assert len(B._keenable_search("k", item)["results"][0]["snippet"]) <= 300
    with mock.patch.object(B, "tinyfish_budget_check", lambda k: None), mock.patch.object(B, "_get_json", lambda *a, **k: (200, {"results": [{"url": "u", "position": 1, "snippet": LONG}]})):
        assert len(B._tinyfish_search("k", item)["results"][0]["snippet"]) <= 300

def test_snippet_contains_budget():
    c = {"type": "snippet_contains", "value": "needle"}
    r = lambda sn, t=None: [{"url": "https://a/", "title": t, "snippet": sn}]
    assert checks.search_check(c, r("x" * 10 + " needle"))[0]
    assert not checks.search_check(c, r("x" * 400 + " needle"))[0]
    assert checks.search_check(c, r("x" * 400, "a needle title"))[0]

# ---- B: urlmatch
RFC = "https://www.rfc-editor.org/rfc/rfc8446"
INFO = lambda n: {f"https://www.rfc-editor.org/rfc/rfc{n}": {"final_url": f"https://www.rfc-editor.org/info/rfc{n}/", "final_status": 200,
                  "chain": [{"url": f"https://www.rfc-editor.org/rfc/rfc{n}", "status": 301}], "resolved_at": "2026-11-23T00:00:00Z", "user_agent": "AgentWebIndexBot/1.0"}}
MDN = "https://developer.mozilla.org/en-US/docs/Web/CSS/flex-direction"
MDN2 = "https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/flex-direction"
PY = "https://docs.python.org/3/library/stdtypes.html"
PY2 = "https://docs.python.org/3/builtins/stdtypes.html"
def entry(final, status=200, hops=1, exp=None):
    return {"final_url": final, "final_status": status, "chain": [{"url": "x", "status": 301}] * hops, "resolved_at": "2026-11-23T00:00:00Z", "user_agent": "AgentWebIndexBot/1.0"}
def M(exp, res, redirects=None): return urlmatch.match(exp, [{"url": res}], redirects=redirects or {})

@pytest.mark.parametrize("exp,res,rules", [
    (RFC, "https://www.rfc-editor.org/rfc/rfc8446.html", ["M01"]), (RFC, "https://www.rfc-editor.org/rfc/rfc8446.txt", ["M01"]),
    ("https://www.rfc-editor.org/rfc/rfc0748", "https://www.rfc-editor.org/rfc/rfc748", ["M01"]),
    ("https://www.rfc-editor.org/rfc/rfc6749", "https://datatracker.ietf.org/doc/html/rfc6749", ["M01"]),
    (RFC, "https://tools.ietf.org/html/rfc8446", ["M01"]),
    (PY, PY + "?highlight=removesuffix", ["R07b"]),
    ("https://arxiv.org/abs/1706.03762", "https://arxiv.org/pdf/1706.03762v7", ["R09"]),
    ("https://arxiv.org/abs/1706.03762", "https://arxiv.org/pdf/1706.03762v7.pdf", ["R09"]),
    ("https://pkg.go.dev/net/http", "https://golang.org/pkg/net/http", ["M02"]),
    ("https://example.com/docs", "https://example.com/docs/index.html", ["R10"]),
    ("https://example.com/", "https://example.com/index.htm", ["R10"]),
    ("https://example.com/p?a=1&b=2", "https://example.com/p?b=2&a=1", ["R08"]),
])
def test_positive(exp, res, rules):
    ok, m, r = M(exp, res); assert ok and m == res and r == rules

def test_positive_via_redirect_log():
    for exp, res, red in ((RFC, "https://www.rfc-editor.org/info/rfc8446/", INFO(8446)), (MDN, MDN2, {MDN: entry(MDN2)}), (PY, PY2, {PY: entry(PY2)})):
        ok, m, r = M(exp, res, red); assert ok and m == res and "RD" in r
    assert not M(RFC, "https://www.rfc-editor.org/info/rfc8446/", None)[0]   # no entry, no match

@pytest.mark.parametrize("exp,res", [
    (RFC, "https://www.rfc-editor.org/info/rfc8446"), (RFC, "https://www.rfc-editor.org/rfc/rfc8446.pdf"),
    ("https://www.rfc-editor.org/rfc/rfc1925", "https://datatracker.ietf.org/doc/rfc1925/"), ("https://www.rfc-editor.org/rfc/rfc1925", "https://datatracker.ietf.org/doc/rfc1925/history"),
    (RFC, "https://www.ietf.org/rfc/rfc8446"), (RFC, "https://datatracker.ietf.org/doc/html/rfc8446.html"), (RFC, "https://datatracker.ietf.org/doc/html/rfc8446.txt"),
    ("https://www.rfc-editor.org/rfc/rfc1149", "https://www.rfc-editor.org/refs/ref1149.txt"), ("https://www.rfc-editor.org/rfc/rfc1149", "https://www.rfc-editor.org/rfc-index"),
    (RFC, "https://www.rfc-editor.org/rfc/rfc9846"), ("https://www.rfc-editor.org/rfc/rfc1149", "https://www.rfc-editor.org/rfc/rfc11490"),
    (RFC, "https://errata.rfc-editor.org/rfc/rfc8446"), (RFC, "https://doi.org/10.17487/rfc8446"),
    ("https://doc.rust-lang.org/std/vec/struct.Vec.html", "https://doc.rust-lang.org/beta/std/vec/struct.Vec.html"),
    ("https://physics.nist.gov/cgi-bin/cuu/Value?c", "https://physics.nist.gov/cgi-bin/cuu/Value?h"),
    ("https://example.com/page?id=1", "https://example.com/page?id=2"), ("https://example.com/a", "https://example.com/a/b"),
    ("https://pkg.go.dev/github.com/x/y", "https://golang.org/pkg/github.com/x/y"),
    ("https://arxiv.org/abs/1706.03762", "https://arxiv.org/abs/1706.03763"), ("https://arxiv.org/abs/1706.03762", "https://arxiv.org/html/1706.03762"),
    ("https://developer.mozilla.org/en-US/docs/Web/CSS/flex", "https://developer.mozilla.org/en-us/docs/Web/CSS/flex"),
    ("https://example.com/a", "not a url"), ("not a url", "not a url"), ("https://example.com/a", "ftp://example.com/a"),
])
def test_negative(exp, res): assert not M(exp, res)[0]

def test_redirect_entry_scoping():
    assert not M("https://www.rfc-editor.org/rfc/rfc7519", "https://www.rfc-editor.org/info/rfc8725", INFO(7519))[0]
    assert M("https://www.rfc-editor.org/rfc/rfc7519", "https://www.rfc-editor.org/info/rfc7519", INFO(7519))[0]
    assert not M(MDN, MDN2, {MDN: entry(MDN2, status=404)})[0]
    assert not M(MDN, MDN2, {MDN: entry(MDN2, hops=0)})[0]
    assert not M(MDN, MDN2, {MDN: entry(MDN)})[0]
    assert not M(MDN, MDN2, {"https://other/": entry(MDN2)})[0]
    # candidate side is never resolved: a result that is not covered by a rule or the expected URL's own log entry does not match
    assert not M("https://www.rfc-editor.org/rfc/rfc8446", "https://tools.ietf.org/doc/rfc8446", INFO(8446))[0]
    assert not M("https://www.rfc-editor.org/rfc/rfc8446", "https://www.rfc-editor.org/info/rfc8446/", {"https://www.rfc-editor.org/info/rfc8446/": entry("https://x/")})[0]

def test_use_redirects_switch_and_missing_file(tmp_path):
    assert urlmatch.load_redirects(tmp_path / "nope.json") == {}
    assert urlmatch.match(RFC, [{"url": "https://www.rfc-editor.org/info/rfc8446/"}], use_redirects=True)[0]
    assert not urlmatch.match(RFC, [{"url": "https://www.rfc-editor.org/info/rfc8446/"}], use_redirects=False)[0]
    assert urlmatch.canonical("http://[bad")[1] == ["unparseable"]

def test_expected_forms_list_and_top():
    f = urlmatch.expected_forms(["https://a.example/x", RFC], INFO(8446))
    assert set(f) == {"https://a.example/x", "rfc:8446", "https://rfc-editor.org/info/rfc8446"}
    res = [{"url": f"https://x.example/{i}"} for i in range(10)] + [{"url": RFC}]
    assert not urlmatch.match(RFC, res, redirects={})[0] and urlmatch.match(RFC, res, top=11, redirects={})[0]

def test_norm_url_unchanged():
    assert norm_url("http://www.Example.com/a/?utm_x=1&b=2#f") == "https://example.com/a?b=2"
    assert norm_url("https://www.rfc-editor.org/rfc/rfc8446.txt") != norm_url(RFC)

# ---- redirect log integrity
def test_redirect_log_integrity():
    d = json.loads((BENCH / "sets" / "url_redirects.json").read_text())
    assert d["schema"] == 1 and isinstance(d["note"], str)
    want = set(redirects.expected_urls("search_evergreen")); assert len(want) > 0 and set(d["entries"]) == want
    for u, e in d["entries"].items():
        assert set(e) == {"final_url", "final_status", "chain", "resolved_at", "user_agent"}, u
        assert isinstance(e["final_url"], str) and isinstance(e["final_status"], int) and e["user_agent"] == "AgentWebIndexBot/1.0"
        assert len(e["resolved_at"]) == 20 and e["resolved_at"].endswith("Z")
        assert all(set(h) == {"url", "status"} and isinstance(h["url"], str) and 300 <= h["status"] < 400 for h in e["chain"]), u

def test_resolver_offline_mocked():
    class Resp:
        def __init__(s, st, loc=None): s.status = st; s.headers = {"Location": loc} if loc else {}
        def __enter__(s): return s
        def __exit__(s, *a): return False
    seq = iter([Resp(301, "/b"), Resp(200)])
    class Op:
        def open(self, req, timeout=None): assert timeout == 20 and req.get_header("User-agent") == "AgentWebIndexBot/1.0"; return next(seq)
    e = redirects.resolve("https://h.example/a", opener=Op())
    assert e["final_url"] == "https://h.example/b" and e["final_status"] == 200 and e["chain"] == [{"url": "https://h.example/a", "status": 301}]
    class Loop:
        def open(self, req, timeout=None): return Resp(302, "/again")
    e = redirects.resolve("https://h.example/a", opener=Loop()); assert len(e["chain"]) == 6 and e["final_status"] == 302

# ---- monotonic + golden
def _logs():
    for p in sorted((BENCH / "runs").glob("*/*/search_evergreen.jsonl")) + sorted((BENCH / "runs").glob("*/*/news_*.jsonl")): yield p

def _sets():
    out = {}
    for p in (BENCH / "sets").glob("search_evergreen.jsonl"): out[p.stem] = {r["id"]: r for r in read_jsonl(p)}
    for p in (BENCH / "sets").glob("news_*.jsonl"): out[p.stem] = {r["id"]: r for r in read_jsonl(p)}
    return out

def _grade(row, res):
    return checks.news_check(row, res)[0] if "alt_check" in row else checks.search_check(row["check"], res)[0]

def test_monotonic_no_v1_pass_lost():
    sets, seen = _sets(), 0
    for p in _logs():
        rows = sets.get(p.stem)
        if not rows: continue
        for rec in read_jsonl(p):
            row = rows.get(rec.get("item_id"))
            if row is None or rec.get("status") not in ("pass", "fail") or row["check"]["type"] != "url_exact" or not rec.get("pass"): continue
            seen += 1   # news rows may have passed through alt_check, so grade the whole row as the runner does
            assert _grade(row, rec.get("results") or []), (p, rec["item_id"])
    assert seen > 0

def test_golden_oct23_v2_rules_only():
    rows = _sets()["search_evergreen"]
    want = {"exa": (57, ["se-doc-001", "se-doc-021", "se-doc-022", "se-doc-023", "se-doc-025", "se-lt-002", "se-lt-003", "se-lt-004", "se-lt-005", "se-lt-006", "se-lt-007", "se-lt-008", "se-lt-009", "se-lt-011", "se-lt-012", "se-lt-014", "se-lt-015"]),
            "tavily": (75, ["se-doc-020", "se-doc-021", "se-doc-022", "se-doc-023", "se-doc-024", "se-lt-003"])}
    with mock.patch.object(urlmatch, "load_redirects", lambda path=None: {}):
        for prov, (total, flips) in want.items():
            n, fl = 0, []
            for rec in read_jsonl(BENCH / "runs" / "2026-10-23" / prov / "search_evergreen.jsonl"):
                row = rows[rec["item_id"]]
                if rec["status"] not in ("pass", "fail"): continue
                ok = _grade(row, rec["results"]); n += ok
                if row["check"]["type"] == "url_exact" and ok and not rec["pass"]: fl.append(rec["item_id"])
            assert n == total and fl == flips, (prov, n, fl)

# ---- rescore tool
def _run_rescore(out, *extra):
    argv = ["--bench-dir", str(BENCH), "--set", "search_evergreen", "--set-file", str(BENCH / "sets/search_evergreen.jsonl"),
            "--log", f"exa={BENCH}/runs/2026-10-23/exa/search_evergreen.jsonl", "--out-csv", str(out), *extra]
    import rescore
    with contextlib.redirect_stdout(io.StringIO()) as so: rescore.main(argv)
    return so.getvalue()

def test_rescore_deterministic_and_offline(tmp_path):
    import rescore, urllib.request, socket
    saved = (urllib.request.urlopen, socket.create_connection, socket.socket.connect, urlmatch.load_redirects)
    try:
        a, b = tmp_path / "a.csv", tmp_path / "b.csv"
        o1 = _run_rescore(a); o2 = _run_rescore(b)
        assert a.read_bytes() == b.read_bytes() and o1 == o2 and "url_exact" in o1 and "sha256" in o1
        with pytest.raises(RuntimeError): urllib.request.urlopen("http://example.invalid/")   # guard installed by main()
        assert a.read_text().splitlines()[0] == "provider,log_path,item_id,check_type,logged_pass,regraded_pass,matched_url,rule_ids"
        c = tmp_path / "c.csv"; _run_rescore(c, "--no-redirects")
        assert c.read_bytes() != a.read_bytes()
    finally:
        urllib.request.urlopen, socket.create_connection, socket.socket.connect, urlmatch.load_redirects = saved
