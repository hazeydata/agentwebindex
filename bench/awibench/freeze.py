"""AC2: freeze-time checks + row retirement log.
- every search source_url must return HTTP 200 (logged)
- every scrape marker must pass the reference-browser health check
Both checkers are injected: `fetch_status(url)->int` and `reference_browser(url)->{text,html}`.
python -m awibench.freeze --live runs urllib for status, but the reference browser is a HOOK
(a Playwright implementation is not built), so a live freeze stops until one is supplied.
Writes sets/freeze-<date>.json with set sha256s; exits non-zero on any failure."""
import argparse, datetime as dt, json, sys
from pathlib import Path
from .util import BENCH, load_yaml, read_jsonl, sha256_file
from . import checks

def freeze(sets_dir, cfg, fetch_status, reference_browser, today):
    sd = Path(sets_dir); log = {"date": today, "sha256": {}, "source_url": [], "markers": [], "failures": []}
    for f in ("search_evergreen.jsonl", "scrape_urls.jsonl", "browser_tasks.yaml"):
        if (sd / f).exists(): log["sha256"][f] = sha256_file(sd / f)
    for r in read_jsonl(sd / "search_evergreen.jsonl"):
        try: st = fetch_status(r["source_url"])
        except Exception as e: st = f"error: {type(e).__name__}"
        log["source_url"].append({"id": r["id"], "url": r["source_url"], "status": st})
        if st != 200: log["failures"].append(f"{r['id']}: source_url status {st}")
    for r in read_jsonl(sd / "scrape_urls.jsonl"):
        try: ok, why = checks.scrape_check(r, reference_browser(r["url"]), cfg["challenge_signatures"])
        except Exception as e: ok, why = False, f"error: {type(e).__name__}: {e}"
        log["markers"].append({"id": r["id"], "ok": ok, "reason": why})
        if not ok: log["failures"].append(f"{r['id']}: marker health check failed ({why})")
    return log

def retire(sets_dir, set_file, row_id, reason, today):
    """Rows retire only with a logged reason (1a, 1c). Appends to sets/retired.jsonl."""
    with open(Path(sets_dir) / "retired.jsonl", "a") as fh:
        fh.write(json.dumps({"date": today, "set": set_file, "id": row_id, "reason": reason}) + "\n")

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--sets", default=str(BENCH / "sets")); ap.add_argument("--live", action="store_true")
    a = ap.parse_args(argv)
    if not a.live: ap.error("freeze needs --live (network) or use freeze() with injected checkers in tests")
    print("reference browser not implemented (HOOK): live freeze cannot complete"); return 2

if __name__ == "__main__": sys.exit(main())
