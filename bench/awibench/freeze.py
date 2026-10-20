"""AC2: freeze-time checks + row retirement log.
- every search source_url must return HTTP 200 (logged)
- every scrape marker must pass the reference-browser health check
Both checkers are injected: `fetch_status(url)->int` and `reference_browser(url)->{text,html}`.
python -m awibench.freeze --live uses urllib for status and headless Chrome (--dump-dom) as the reference browser.
Writes sets/freeze-<date>.json with set sha256s; exits non-zero on any failure."""
import argparse, datetime as dt, json, os, shutil, subprocess, sys, tempfile, urllib.request
from html.parser import HTMLParser
from pathlib import Path
from .util import BENCH, load_yaml, read_jsonl, sha256_file
from . import checks

def _scrape(r, browse, cfg):
    try: return checks.scrape_check(r, browse(r["url"]), cfg["challenge_signatures"], cfg.get("challenge_html_signatures", ()))
    except Exception as e: return False, f"error: {type(e).__name__}: {e}"

def _status(url, fetch):
    try: return fetch(url)
    except Exception as e: return f"error: {type(e).__name__}"

def freeze(sets_dir, cfg, fetch_status, reference_browser, today, retry_status=None, retry_browser=None):
    """A failed row is retried exactly once with retry_status/retry_browser when given; it passes if either attempt passes."""
    sd = Path(sets_dir); log = {"date": today, "sha256": {}, "source_url": [], "markers": [], "failures": []}
    for f in ("search_evergreen.jsonl", "scrape_urls.jsonl", "browser_tasks.yaml"):
        if (sd / f).exists(): log["sha256"][f] = sha256_file(sd / f)
    for r in read_jsonl(sd / "search_evergreen.jsonl"):
        att = [_status(r["source_url"], fetch_status)]
        if att[0] != 200 and retry_status: att.append(_status(r["source_url"], retry_status))
        st = 200 if 200 in att else att[-1]
        log["source_url"].append({"id": r["id"], "url": r["source_url"], "status": st, "attempts": att})
        if st != 200: log["failures"].append(f"{r['id']}: source_url status {' / '.join(map(str, att))}")
    for r in read_jsonl(sd / "scrape_urls.jsonl"):
        att = [_scrape(r, reference_browser, cfg)]
        if not att[0][0] and retry_browser: att.append(_scrape(r, retry_browser, cfg))
        ok = any(a[0] for a in att); why = next((a[1] for a in att if a[0]), att[-1][1])
        log["markers"].append({"id": r["id"], "ok": ok, "reason": why, "attempts": [{"ok": a[0], "reason": a[1]} for a in att]})
        if not ok: log["failures"].append(f"{r['id']}: marker health check failed ({' / '.join(a[1] for a in att)})")
    return log

def retire(sets_dir, set_file, row_id, reason, today):
    """Rows retire only with a logged reason (1a, 1c). Appends to sets/retired.jsonl."""
    with open(Path(sets_dir) / "retired.jsonl", "a") as fh:
        fh.write(json.dumps({"date": today, "set": set_file, "id": row_id, "reason": reason}) + "\n")

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36 AWI-Freeze/1"
HARNESS_UA = "AgentWebIndexBot/1.0"

class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "template"}
    def __init__(self): super().__init__(); self.skip = 0; self.buf = []
    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP: self.skip += 1
    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip: self.skip -= 1
    def handle_data(self, d):
        if not self.skip: self.buf.append(d)

def html_text(html):
    p = _Text(); p.feed(html); return " ".join(p.buf)

def http_status(url, timeout=30, ua=None):
    req = urllib.request.Request(url, headers={"User-Agent": ua or UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.status
    except urllib.error.HTTPError as e: return e.code

def chrome_reference_browser(url, binary="google-chrome", budget_ms=10000, timeout=90, ua=None):
    ud = tempfile.mkdtemp(prefix="awi-chrome-")
    try:
        p = subprocess.run([binary, f"--user-data-dir={ud}", "--no-first-run", "--headless=new", "--no-sandbox", "--disable-gpu", f"--user-agent={ua or UA}",
                            f"--virtual-time-budget={budget_ms}", "--dump-dom", url], capture_output=True, text=True, timeout=timeout)
    finally: shutil.rmtree(ud, ignore_errors=True)
    return {"html": p.stdout, "text": html_text(p.stdout)}

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--sets", default=str(BENCH / "sets")); ap.add_argument("--live", action="store_true")
    ap.add_argument("--chrome", default=os.environ.get("AWI_CHROME", "google-chrome"))
    a = ap.parse_args(argv)
    if not a.live: ap.error("freeze needs --live (network) or use freeze() with injected checkers in tests")
    today = dt.datetime.now().astimezone().date().isoformat()
    log = freeze(a.sets, load_yaml(BENCH / "config.yaml"), http_status, lambda u: chrome_reference_browser(u, a.chrome), today,
                 lambda u: http_status(u, ua=HARNESS_UA), lambda u: chrome_reference_browser(u, a.chrome, ua=HARNESS_UA))
    out = Path(a.sets) / f"freeze-{today}.json"; out.write_text(json.dumps(log, indent=1) + "\n")
    print(json.dumps({"out": out.name, "sha256": log["sha256"], "source_url": len(log["source_url"]), "markers": len(log["markers"]), "failures": log["failures"]}, indent=1))
    return 1 if log["failures"] else 0

if __name__ == "__main__": sys.exit(main())
