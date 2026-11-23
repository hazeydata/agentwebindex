"""url_exact equivalence (SPEC-bench 3a, issue #60; rules ported from the ruled awi-method-v2 R2 FINAL / R3 rescore).

canonical(url) starts from util.norm_url (unchanged) and applies a CLOSED, ordered rule list:
  R07b drop the display-only query param `highlight`      R08 sort remaining query params
  R10  /index.html|.htm -> directory                       R09 arxiv /abs|/pdf/ID[vN][.pdf] -> arxiv.org/abs/ID
  M01  RFC text mirrors -> rfc:N                           M02 golang.org/pkg/<stdlib> -> pkg.go.dev/<stdlib>
  RD   the EXPECTED url's logged redirect target (bench/sets/url_redirects.json); candidates are never resolved.
Equality is exact string equality of canonical forms. No fuzzy matching, no network at grade time."""
import json, re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from .util import BENCH, norm_url

REDIRECTS_PATH = BENCH / "sets" / "url_redirects.json"
_cache = {}

def canonical(url):
    """-> (canonical string, [rule ids]); malformed or non-http(s) -> (url, ['unparseable'])."""
    try:
        if not isinstance(url, str): return url, ["unparseable"]
        raw = urlsplit(url.strip())
        if raw.scheme.lower() not in ("http", "https") or not raw.hostname: return url, ["unparseable"]
        raw.port   # raises ValueError on a bad port
        s = urlsplit(norm_url(url))
    except ValueError:
        return url, ["unparseable"]
    host, path, rules = s.netloc, s.path or "/", []
    pairs = parse_qsl(s.query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if k != "highlight"]
    if len(kept) != len(pairs): rules.append("R07b")
    if kept != sorted(kept): rules.append("R08")
    q = urlencode(sorted(kept))
    m = re.fullmatch(r"(.*/)index\.html?", path)
    if m: path = m.group(1).rstrip("/") or "/"; rules.append("R10")
    if host == "arxiv.org":
        m = re.fullmatch(r"/(abs|pdf)/(.+?)(v\d+)?(\.pdf)?", path)
        if m and not (m.group(1) == "abs" and m.group(4)):
            if m.group(1) != "abs" or m.group(3): rules.append("R09")
            return f"https://arxiv.org/abs/{m.group(2)}", rules
    for h, pat in (("rfc-editor.org", r"/rfc/rfc(\d+)(?:\.txt|\.html)?"), ("datatracker.ietf.org", r"/doc/html/rfc(\d+)"), ("tools.ietf.org", r"/html/rfc(\d+)")):
        m = host == h and re.fullmatch(pat, path)
        if m:
            if not (h == "rfc-editor.org" and path == f"/rfc/rfc{int(m.group(1))}"): rules.append("M01")
            return f"rfc:{int(m.group(1))}", rules
    m = host == "golang.org" and re.fullmatch(r"/pkg/([^./][^/]*(?:/[^/]+)*)", path)
    if m and "." not in m.group(1).split("/")[0]: host, path = "pkg.go.dev", "/" + m.group(1); rules.append("M02")
    return urlunsplit(("https", host, path, q, "")), rules

def load_redirects(path=None):
    """entries of bench/sets/url_redirects.json ({} when the file is missing); cached per path."""
    p = Path(path) if path else REDIRECTS_PATH
    if p not in _cache:
        _cache[p] = json.loads(p.read_text()).get("entries", {}) if p.is_file() else {}
    return _cache[p]

def _usable(expected, e):
    return (isinstance(e, dict) and e.get("final_status") == 200 and isinstance(e.get("chain"), list) and len(e["chain"]) >= 1
            and isinstance(e.get("final_url"), str) and e["final_url"] != expected)

def expected_forms(values, redirects=None):
    """-> {canonical: (expected_url, rules)}; the as-written form first, then the logged redirect target (rules + ['RD'])."""
    forms = {}
    vals = [values] if isinstance(values, str) else list(values)
    for v in vals:
        c, r = canonical(v)
        if "unparseable" not in r: forms.setdefault(c, (v, r))
    for v in vals:
        e = (redirects or {}).get(v)
        if _usable(v, e):
            c, r = canonical(e["final_url"])
            if "unparseable" not in r: forms.setdefault(c, (v, r + ["RD"]))
    return forms

def match(values, results, top=10, redirects=None, use_redirects=True):
    """-> (ok, matched result url | None, rule ids fired on either side). Candidates are never resolved."""
    if not use_redirects: redirects = {}
    elif redirects is None: redirects = load_redirects()
    forms = expected_forms(values, redirects)
    for r in results[:top]:
        u = r.get("url")
        if not isinstance(u, str): continue
        c, rules = canonical(u)
        if "unparseable" in rules or c not in forms: continue
        er = forms[c][1]
        return True, u, list(dict.fromkeys(rules + er))
    return False, None, []
