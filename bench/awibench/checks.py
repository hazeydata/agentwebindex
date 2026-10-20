"""Pass/fail checks for each set type (1a-1d)."""
import datetime as dt, hashlib
from html.parser import HTMLParser
from .util import norm_url, domain, norm_text

GRADER_VERSION = "canon-2026-10-20"

def search_check(check, results, published_at=None, now=None):
    """results: list of {url,title,snippet,published_at?} in rank order. Returns (pass, reason)."""
    t, v = check["type"], check["value"]
    if t == "url_exact":
        ok = norm_url(v) in [norm_url(r["url"]) for r in results[:10]]; return ok, "url_exact in top10" if ok else "url_exact not in top10"
    if t == "url_domain":
        hits = [r for r in results[:5] if domain(r["url"]) == domain(v if "//" in v else "https://" + v)]
        if check.get("within_h") and published_at:
            ref = dt.datetime.fromisoformat(published_at)
            hits = [r for r in hits if r.get("published_at") and abs((dt.datetime.fromisoformat(r["published_at"]) - ref).total_seconds()) <= check["within_h"] * 3600]
        return bool(hits), "domain in top5" if hits else "domain not in top5"
    if t == "snippet_contains":
        nv = norm_text(v); ok = any(nv in norm_text((r.get("title") or "") + " " + (r.get("snippet") or "")) for r in results[:5])
        return ok, "snippet match" if ok else "snippet not found in top5"
    return False, f"unknown check {t}"

def news_check(row, results):
    ok, why = search_check(row["check"], results)
    if ok: return ok, why
    return search_check(row["alt_check"], results, row["published_at"])

class _Sel(HTMLParser):
    """Collects text inside elements matching a simple selector: tag, #id, .class, tag.class."""
    def __init__(self, sel):
        super().__init__(); self.depth = 0; self.buf = []
        self.tag, _, self.cls = sel.partition("."); self.id = None
        if self.tag.startswith("#"): self.id, self.tag = self.tag[1:], ""
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.depth: self.depth += 1; return
        if (not self.tag or tag == self.tag) and (not self.cls or self.cls in (a.get("class") or "").split()) and (not self.id or a.get("id") == self.id):
            self.depth = 1
    def handle_endtag(self, tag):
        if self.depth: self.depth -= 1
    def handle_data(self, d):
        if self.depth: self.buf.append(d)

def scrape_check(row, output, challenge_signatures, html_signatures=()):
    """output: {text, html?}. Pass = marker found AND len(text) >= min_chars AND no challenge signature
    (challenge_signatures match the text, html_signatures match the raw html)."""
    text = output.get("text") or ""
    low, hlow = text.casefold(), (output.get("html") or "").casefold()
    for sig in challenge_signatures:
        if sig.casefold() in low: return False, f"challenge page signature: {sig}"
    for sig in html_signatures:
        if sig.casefold() in hlow: return False, f"challenge page signature: {sig}"
    m = row["marker"]
    if m["type"] == "text_contains": found = norm_text(m["value"]) in norm_text(text)
    else:
        sel, _, want = m["value"].partition("::")
        p = _Sel(sel); p.feed(output.get("html") or ""); found = norm_text(want) in norm_text(" ".join(p.buf)) if want else bool(p.buf)
    if not found: return False, "marker not found"
    if len(text) < row["min_chars"]: return False, f"text {len(text)} < min_chars {row['min_chars']}"
    return True, "marker found, length ok"

def browser_check(task, output):
    t, v = task["pass"]["type"], task["pass"]["value"]
    if t == "final_url_matches": ok = norm_url(output.get("final_url", "")) == norm_url(v)
    elif t == "extracted_value_equals": ok = str(output.get("extracted_value")) == str(v)
    elif t == "file_sha256": ok = output.get("file_bytes") is not None and hashlib.sha256(output["file_bytes"]).hexdigest() == v
    elif t == "dom_text_contains": ok = norm_text(v) in norm_text(output.get("dom_text", ""))
    else: return False, f"unknown pass type {t}"
    return ok, f"{t} {'ok' if ok else 'mismatch'}"
