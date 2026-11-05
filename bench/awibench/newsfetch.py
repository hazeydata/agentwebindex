"""Browser fetch task set derived from a frozen weekly news set (no new rows, no edits to the frozen news file).

Weeks <= 2026-45 (LEGACY_LAST_WEEK): each url_exact news row becomes one task whose pass value is the feed
headline (dom_text_contains). The set sha is the frozen news file's sha. Rows are byte-identical to that rule.
expected_source on the set object is feed_title. The task rows do not carry that key.

Weeks >= 2026-46 (method (a), AWI-NEWSFETCH-FIX-001): the set build reads the article with plain HTTP
(no provider call) and sets pass.value to text it verified on the visible page. Fallback order:
the first non-empty h1; else og:title only when that text is also in the visible body; else <title>
with a trailing " - Site" / " | Site" suffix removed, only when the trimmed text is in the visible body.
Visible text is the body with script, style, noscript, template and head left out, so a string that
appears only in script or head does not count. Stored text is HTML-unescaped, Unicode NFC, and
whitespace-collapsed. Case, quotes and dashes are kept. It is capped at EXPECTED_TEXT_MAX (300)
characters on a word boundary so it stays a substring of the page text. Grade time
(checks.dom_grade_text) decodes entities the same way, applies NFC, then util.norm_text
(casefold, punctuation including quotes and dashes to spaces, collapse whitespace).

A row that cannot be verified is excluded, never emitted with the feed title. Reason codes:
fetch_http_4xx (any HTTP 4xx, including 404 and 410; not retried), fetch_http_5xx (any HTTP 5xx;
one retry, then exclude), timeout (not retried), paywall (HTTP 200, a paywall marker in the visible
text, and no h1 / og:title / title candidate in the body), expected_text_not_on_page (HTTP 200, no
candidate, no paywall marker), fetch_error (any other fetch failure, including a non-http(s) URL).
A paywall marker does not exclude a row whose h1 (or later fallback) is on the page.
Exclusions are written to newsfetch_<week>.excluded.json. Emitted rows plus exclusions equal the
url_exact news rows. The derived newsfetch_<week>.jsonl is frozen (its own sha). The news file sha
is stored in newsfetch_<week>.meta.json and on each run log line as news_sha. A frozen file is not
rebuilt, so a URL that starts answering later is not added back.

Page fetch uses urllib the same way freeze.plain_fetch does (http/https only; redirects are the
ones urllib follows: http, https or ftp, at most 10, at most 4 repeats of one URL). If the final
URL's domain (util.domain) differs from the start URL, the row records final_url and domain_changed.
"""
import argparse, hashlib, html, json, re, sys, unicodedata
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from .util import read_jsonl, sha256_file, sha256_text, domain

LEGACY_LAST_WEEK = "2026-45"     # weeks <= this keep the feed headline and the news-file sha
EXPECTED_TEXT_MAX = 300          # stored pass.value cap, on a word boundary
PAGE_FETCH_TIMEOUT_S = 20        # set-build plain HTTP timeout; a timeout excludes the row (no retry)
MAX_5XX_ATTEMPTS = 2             # one try plus one retry, then fetch_http_5xx
_WEEK = re.compile(r"news_(\d{4}-\d{2})$")
_SITE_SUFFIX = re.compile(r"\s+[-|–—]\s+\S(?:.{0,80})?$")
PAYWALL_MARKERS = (
    "subscribe to continue", "subscribe to read", "sign in to continue",
    "already a subscriber", "this article is for subscribers", "to continue reading",
)
DROP_TAGS = {"script", "style", "noscript", "template"}

class NotFrozen(Exception):
    """A week >= 2026-46 has no frozen newsfetch file yet. The benchmark does not fetch pages itself."""

def week_id(path):
    m = _WEEK.search(Path(path).stem)
    if not m: raise ValueError(f"not a news set name: {Path(path).name}")
    return m.group(1)

def verifies_on_page(week):
    return week > LEGACY_LAST_WEEK

def set_name_for(path):
    return "newsfetch_" + Path(path).stem.removeprefix("news_")

def tasks(news_rows, timeout_s=30):
    """Legacy rows (<= 2026-45): pass value is the feed headline. Do not use this for later weeks."""
    return [{"id": r["id"], "start_url": r["check"]["value"], "goal": "load the article and return its rendered text",
             "pass": {"type": "dom_text_contains", "value": r["headline"]}, "timeout_s": timeout_s, "category": "news_fetch"}
            for r in news_rows if (r.get("check") or {}).get("type") == "url_exact"]

def normalize_expected(s):
    """Stored expected text: unescape, NFC, collapse whitespace. Case and punctuation stay."""
    s = unicodedata.normalize("NFC", html.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()

def cap_expected(s, limit=EXPECTED_TEXT_MAX):
    """Cap on a word boundary. The result is a prefix of the normalized text, so it stays a substring.
    A single word longer than the cap is cut at the cap (still a prefix)."""
    s = normalize_expected(s)
    if len(s) <= limit: return s
    cut = s[:limit]
    sp = cut.rfind(" ")
    if sp > 0: cut = cut[:sp]
    return cut

def trim_site_suffix(title):
    return _SITE_SUFFIX.sub("", normalize_expected(title)).strip()

class PageText(HTMLParser):
    """Visible body text plus the h1 / og:title / title candidates. Head and script are not visible.
    Nested tags inside an h1 (em, span) stay inside that h1; only </h1> closes it."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.drop = 0; self.in_head = 0; self.in_title = False; self.in_h1 = False
        self.h1_buf = []; self.h1s = []; self.body = []; self.title_buf = []; self.og_title = None
    def handle_starttag(self, tag, attrs):
        if tag in DROP_TAGS: self.drop += 1; return
        if self.drop: return
        if tag == "head": self.in_head += 1
        a = dict(attrs)
        if tag == "title" and self.in_head: self.in_title = True
        if tag == "meta" and self.in_head:
            prop = (a.get("property") or a.get("name") or "").lower()
            if prop == "og:title" and self.og_title is None and a.get("content"): self.og_title = a["content"]
        if tag == "h1" and not self.in_head and not self.in_h1:
            self.in_h1 = True; self.h1_buf = []
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
    def handle_endtag(self, tag):
        if tag in DROP_TAGS and self.drop: self.drop -= 1; return
        if self.drop: return
        if tag == "title": self.in_title = False
        if tag == "head" and self.in_head: self.in_head -= 1
        if tag == "h1" and self.in_h1:
            self.in_h1 = False
            text = normalize_expected("".join(self.h1_buf))
            if text: self.h1s.append(text)
    def handle_data(self, data):
        if self.drop: return
        if self.in_title: self.title_buf.append(data)
        if self.in_head: return
        if self.in_h1: self.h1_buf.append(data)
        self.body.append(data)
    def visible(self):
        return normalize_expected("".join(self.body))
    def title(self):
        return normalize_expected("".join(self.title_buf))

def parse_page(html_text):
    p = PageText()
    try: p.feed(html_text or ""); p.close()
    except Exception: pass
    return p

def pick_expected(page):
    """First verified candidate, or None. Each candidate must be a substring of the visible body."""
    from .checks import dom_grade_text
    visible = page.visible()
    graded = dom_grade_text(visible)
    def ok(raw):
        c = cap_expected(raw)
        return c if c and c in visible and dom_grade_text(c) and dom_grade_text(c) in graded else None
    for h in page.h1s:
        c = ok(h)
        if c: return c
    c = ok(page.og_title or "")
    if c: return c
    return ok(trim_site_suffix(page.title()))

def _paywalled(visible):
    low = visible.casefold()
    return any(m in low for m in PAYWALL_MARKERS)

def _domain_changed(start, final):
    if not final: return False
    return domain(start) != domain(final)

def derive_row(row, fetch, timeout_s=30):
    """Returns (task, None) or (None, exclusion). fetch(url) -> {status, html, final_url, error?} or raises TimeoutError."""
    url = row["check"]["value"]
    attempts = 0; last = None
    while attempts < MAX_5XX_ATTEMPTS:
        attempts += 1
        try: last = fetch(url) or {}
        except TimeoutError:
            return None, _excl(row, url, "timeout", None, url, attempts)
        status = last.get("status"); err = last.get("error")
        if err == "url_scheme" or (status is None and err):
            return None, _excl(row, url, "fetch_error", status, last.get("final_url") or url, attempts)
        if isinstance(status, int) and 400 <= status <= 499:
            return None, _excl(row, url, "fetch_http_4xx", status, last.get("final_url") or url, attempts)
        if isinstance(status, int) and 500 <= status <= 599:
            if attempts < MAX_5XX_ATTEMPTS: continue
            return None, _excl(row, url, "fetch_http_5xx", status, last.get("final_url") or url, attempts)
        break
    status = (last or {}).get("status"); final = (last or {}).get("final_url") or url
    if not isinstance(status, int) or not (200 <= status <= 299):
        return None, _excl(row, url, "fetch_error", status, final, attempts)
    if urlsplit(final).scheme not in ("http", "https"):
        return None, _excl(row, url, "fetch_error", status, final, attempts)
    page = parse_page((last or {}).get("html") or "")
    expected = pick_expected(page)
    changed = _domain_changed(url, final)
    if not expected:
        reason = "paywall" if _paywalled(page.visible()) else "expected_text_not_on_page"
        return None, _excl(row, url, reason, status, final, attempts) | {"domain_changed": changed}
    task = {"id": row["id"], "start_url": url, "goal": "load the article and return its rendered text",
            "pass": {"type": "dom_text_contains", "value": expected}, "timeout_s": timeout_s, "category": "news_fetch",
            "expected_source": "article_page", "final_url": final, "domain_changed": changed}
    return task, None

def _excl(row, url, reason, status, final, attempts):
    return {"id": row["id"], "reason": reason, "url": url, "final_url": final, "http_status": status,
            "attempts": attempts, "domain_changed": _domain_changed(url, final)}

def derive(news_rows, fetch, timeout_s=30):
    rows, excluded = [], []
    for r in news_rows:
        if (r.get("check") or {}).get("type") != "url_exact": continue
        task, ex = derive_row(r, fetch, timeout_s)
        if ex: excluded.append(ex)
        else: rows.append(task)
    return rows, excluded

def rows_jsonl(rows):
    return "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows)

def _paths(news_path):
    p = Path(news_path); name = set_name_for(p)
    return name, p.with_name(name + ".jsonl"), p.with_name(name + ".meta.json"), p.with_name(name + ".excluded.json")

def load_frozen(news_path):
    name, dest, meta_p, ex_p = _paths(news_path)
    if not (dest.exists() and meta_p.exists() and ex_p.exists()):
        raise NotFrozen(f"{name}.jsonl is not frozen; run python -m awibench.newsfetch")
    blob = dest.read_bytes(); sha = hashlib.sha256(blob).hexdigest()
    meta = json.loads(meta_p.read_text()); news_sha = sha256_file(news_path)
    if meta.get("sha256") != sha or meta.get("news_sha") != news_sha or meta.get("expected_source") != "article_page":
        raise NotFrozen(f"{name}.jsonl does not match its meta")
    excluded = json.loads(ex_p.read_text())
    return name, {"rows": read_jsonl(dest), "sha": sha, "news_sha": news_sha, "expected_source": "article_page",
                  "excluded": excluded.get("rows") or []}

def write_frozen(news_path, rows, excluded):
    """Freeze the derived set. Refuses to overwrite. Returns the same shape as load_frozen."""
    name, dest, meta_p, ex_p = _paths(news_path)
    if dest.exists() or meta_p.exists() or ex_p.exists():
        raise FileExistsError(f"{name}.jsonl is frozen and may not be overwritten")
    blob = rows_jsonl(rows); dest.write_text(blob); sha = sha256_text(blob); news_sha = sha256_file(news_path)
    meta = {"set": name, "news_file": Path(news_path).name, "news_sha": news_sha, "sha256": sha,
            "expected_source": "article_page", "method": "a", "count": len(rows), "excluded_count": len(excluded),
            "url_exact_rows": len(rows) + len(excluded)}
    meta_p.write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n")
    ex_p.write_text(json.dumps({"set": name, "news_file": Path(news_path).name, "news_sha": news_sha, "method": "a",
                                "rows": excluded}, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    return load_frozen(news_path)

def from_news_file(path, timeout_s=30, fetch=None, write=False):
    """Returns (set_name, set). Weeks <= 2026-45 never call fetch. Later weeks load the frozen file, or
    build with the injected fetch. write=True freezes the derived files next to the news file.
    A frozen file is loaded as-is: fetch is not called, so a row excluded at freeze stays excluded."""
    p = Path(path); week = week_id(p); name = set_name_for(p)
    if not verifies_on_page(week):
        rows = tasks(read_jsonl(p), timeout_s); news_sha = sha256_file(p)
        return name, {"rows": rows, "sha": news_sha, "news_sha": news_sha, "expected_source": "feed_title", "excluded": []}
    frozen = p.with_name(name + ".jsonl")
    if frozen.exists():
        if write: raise FileExistsError(f"{name}.jsonl is frozen and may not be overwritten")
        return load_frozen(p)
    if fetch is None:
        raise NotFrozen(f"{name}.jsonl is not frozen; run python -m awibench.newsfetch")
    rows, excluded = derive(read_jsonl(p), fetch, timeout_s)
    if write: return write_frozen(p, rows, excluded)
    blob = rows_jsonl(rows)
    return name, {"rows": rows, "sha": sha256_text(blob), "news_sha": sha256_file(p), "expected_source": "article_page",
                  "excluded": excluded}

def news_weeks_for_run(news_names, named):
    """news_names is sorted. named is None (run the latest week) or the --only-sets names.
    Naming news_X or newsfetch_X keeps that week so the derived fetch set can be built.
    Naming no news week keeps none."""
    if not news_names: return []
    if named is None: return news_names[-1:]
    want = []
    for k in news_names:
        derived = "newsfetch_" + k.removeprefix("news_")
        if k in named or derived in named: want.append(k)
    return want

def keep_named_sets(sets, named):
    """--only-sets news_X also keeps newsfetch_X when it was derived. newsfetch_X alone does not keep news_X.
    Each name is kept at most once."""
    out = {}
    for name in named:
        if name in sets and name not in out: out[name] = sets[name]
        if name.startswith("news_") and not name.startswith("newsfetch_"):
            derived = "newsfetch_" + name.removeprefix("news_")
            if derived in sets and derived not in out: out[derived] = sets[derived]
    return out

def prepare_run_sets(sets, only_sets, load_newsfetch):
    """Drop news weeks this run is not scoring, attach the derived newsfetch set, then apply --only-sets.
    load_newsfetch(news_name) -> (fname, set) and may raise NotFrozen. Returns (sets, skips)."""
    news = sorted(k for k in list(sets) if k.startswith("news_") and not k.startswith("newsfetch_"))
    named = None if only_sets is None else [n.strip() for n in str(only_sets).split(",") if n.strip()]
    keep = news_weeks_for_run(news, named); skips = []
    for old in news:
        if old not in keep: sets.pop(old, None)
    for nk in keep:
        try: fname, fset = load_newsfetch(nk)
        except NotFrozen as e:
            skips.append({"scope": "set", "set": "newsfetch_" + nk.removeprefix("news_"), "status": "skipped_invalid_set", "reason": str(e)})
            continue
        sets[fname] = fset | {"flags": (sets.get(nk) or {}).get("flags", [])}
    if named is not None: sets = keep_named_sets(sets, named)
    return sets, skips

def plain_fetch(url, timeout=PAGE_FETCH_TIMEOUT_S):
    """Plain HTTP GET. http and https only (file:, ftp: and other schemes are refused before any open).
    Redirects are urllib's (freeze.plain_fetch uses the same opener): http, https or ftp, max 10, max 4 repeats."""
    import socket, urllib.request, urllib.error
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return {"status": None, "html": "", "final_url": url, "error": "url_scheme"}
    req = urllib.request.Request(url, headers={"User-Agent": "AgentWebIndexBot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"status": getattr(r, "status", 200), "html": r.read().decode("utf-8", "replace"), "final_url": r.geturl()}
    except urllib.error.HTTPError as e:
        try: body = e.read().decode("utf-8", "replace")
        except Exception: body = ""
        return {"status": e.code, "html": body, "final_url": e.geturl() if hasattr(e, "geturl") else url}
    except TimeoutError: raise
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), (TimeoutError, socket.timeout)): raise TimeoutError(str(e)) from e
        return {"status": None, "html": "", "final_url": url, "error": f"URLError: {e.reason}"}
    except socket.timeout as e:
        raise TimeoutError(str(e)) from e

def _latest_news(sets_dir):
    files = sorted(Path(sets_dir).glob("news_*.jsonl"))
    files = [f for f in files if _WEEK.search(f.stem)]
    if not files: raise SystemExit("no news_YYYY-WW.jsonl in " + str(sets_dir))
    return files[-1]

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--from", dest="src"); ap.add_argument("--latest", action="store_true")
    ap.add_argument("--sets", default=None); a = ap.parse_args(argv)
    from .util import BENCH
    if a.latest: src = _latest_news(a.sets or (BENCH / "sets"))
    elif a.src: src = Path(a.src)
    else: ap.error("need --from FILE or --latest")
    week = week_id(src)
    if not verifies_on_page(week):
        print(f"legacy week {week}: feed_title, no page fetch"); return 0
    name, s = from_news_file(src, fetch=plain_fetch, write=True)
    print(json.dumps({"set": name, "rows": len(s["rows"]), "excluded": len(s["excluded"]), "sha": s["sha"], "news_sha": s["news_sha"]}))
    return 0

if __name__ == "__main__": sys.exit(main())
