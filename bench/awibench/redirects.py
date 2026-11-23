"""Redirect resolver for url_exact expected URLs (issue #60). Run by hand, never at grade time:
    python -m awibench.redirects resolve --set search_evergreen [--out PATH]
Plain unauthenticated GETs to the expected URLs of the set; records each hop's url and status only (no bodies, no headers)."""
import argparse, datetime as dt, json, sys, urllib.request, urllib.error, urllib.parse
from pathlib import Path
from .util import BENCH, read_jsonl

USER_AGENT = "AgentWebIndexBot/1.0"
TIMEOUT_S, MAX_REDIRECTS = 20, 5
DEFAULT_OUT = BENCH / "sets" / "url_redirects.json"
NOTE = ("How each url_exact expected URL resolved when fetched (GET, no auth, <=5 redirects). chain = the redirect hops (url, status). "
        "awibench.urlmatch uses an entry only when final_status is 200, there is >=1 hop and final_url differs (rule RD); candidates are never resolved.")

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None

def resolve(url, opener=None):
    """-> {final_url, final_status, chain:[{url,status}], resolved_at, user_agent}. Errors give final_status 0 (or the HTTP code)."""
    opener = opener or urllib.request.build_opener(_NoRedirect)
    cur, chain, status = url, [], 0
    for _ in range(MAX_REDIRECTS + 1):
        req = urllib.request.Request(cur, method="GET", headers={"User-Agent": USER_AGENT})
        loc = None
        try:
            with opener.open(req, timeout=TIMEOUT_S) as r: status, loc = r.status, r.headers.get("Location")
        except urllib.error.HTTPError as e: status, loc = e.code, e.headers.get("Location")
        except Exception: status, loc = 0, None
        if 300 <= status < 400 and loc:
            chain.append({"url": cur, "status": status})
            if len(chain) > MAX_REDIRECTS: break
            cur = urllib.parse.urljoin(cur, loc); continue
        break
    return {"final_url": cur, "final_status": status, "chain": chain,
            "resolved_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "user_agent": USER_AGENT}

def expected_urls(set_name):
    out = []
    for row in read_jsonl(BENCH / "sets" / f"{set_name}.jsonl"):
        c = row.get("check") or {}
        if c.get("type") == "url_exact":
            out += [c["value"]] if isinstance(c["value"], str) else list(c["value"])
    return sorted(set(out))

def write(path, entries, note=NOTE):
    Path(path).write_text(json.dumps({"schema": 1, "note": note, "entries": entries}, indent=1, sort_keys=True, ensure_ascii=False) + "\n")

def main(argv=None):
    ap = argparse.ArgumentParser(prog="awibench.redirects"); sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("resolve"); p.add_argument("--set", required=True); p.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args(argv)
    out = Path(a.out); old = json.loads(out.read_text()).get("entries", {}) if out.is_file() else {}
    for u in expected_urls(a.set):
        old[u] = resolve(u); print(old[u]["final_status"], len(old[u]["chain"]), u, file=sys.stderr)
    write(out, old); print(f"wrote {len(old)} entries to {out}")

if __name__ == "__main__": main()
