#!/usr/bin/env python3
"""Stop check for the snippet-rerun workflow (stdlib only). Exit 1 (printing provider + status + reason only) when a provider
status is not `ran`, a status/reason mentions a 402, quota, cap, paid usage, budget, rate limit or free-tier stop, a log line has
http_status 402/429, or a log does not have exactly 43 lines. Usage: snippet_rerun_check.py [RUN_DATE] [BENCH_DIR]"""
import datetime as dt, json, re, sys
from pathlib import Path

PROVIDERS = ("exa", "tavily"); SET = "search_evergreen"; EXPECT = 43
BAD_STATUS = re.compile(r"^(stale_|stopped_|skipped_)")
BAD_TEXT = re.compile(r"\b402\b|quota|\bcap\b|paid[ _]usage|budget|rate[ _-]?limit|free[ _-]?tier", re.I)

def check(run_dir):
    run_dir = Path(run_dir); problems = []
    try: summary = json.loads((run_dir / "_run-offset0.json").read_text())
    except (OSError, ValueError) as e: return [f"run summary: unreadable ({type(e).__name__})"]
    seen = {}
    for p in summary.get("providers", []):
        pid = p.get("provider")
        if pid not in PROVIDERS: continue
        seen[pid] = p; st = str(p.get("status")); why = str(p.get("reason") or "")
        if st != "ran" or BAD_STATUS.match(st) or BAD_TEXT.search(st) or BAD_TEXT.search(why):
            problems.append(f"{pid}: status={st} reason={why[:200]}")
    for pid in PROVIDERS:
        if pid not in seen: problems.append(f"{pid}: no status in run summary")
        f = run_dir / pid / f"{SET}.jsonl"
        try: lines = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        except (OSError, ValueError) as e: problems.append(f"{pid}: log unreadable ({type(e).__name__})"); continue
        if len(lines) != EXPECT: problems.append(f"{pid}: log has {len(lines)} lines, expected {EXPECT}")
        for r in lines:
            if r.get("http_status") in (402, 429): problems.append(f"{pid}: log line status={r.get('status')} http_status={r.get('http_status')}")
    return problems

def main(argv):
    day = argv[1] if len(argv) > 1 else dt.datetime.now(dt.timezone.utc).date().isoformat()
    bench = Path(argv[2]) if len(argv) > 2 else Path(__file__).resolve().parent.parent
    problems = check(bench / "runs" / day)
    for p in problems: print("STOP:", p)
    if problems: return 1
    print(f"snippet-rerun check ok ({day}): exa and tavily ran, {EXPECT} lines each"); return 0

if __name__ == "__main__": sys.exit(main(sys.argv))
