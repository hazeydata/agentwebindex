#!/usr/bin/env python3
"""Offline rescore of logged search results with a given awibench (issue #60). Stdlib only; NO network (urlopen is patched to raise).
  python3 bench/tools/rescore.py --bench-dir BENCH --set search_evergreen --set-file SET.jsonl \
      --log exa=runs/.../exa/search_evergreen.jsonl [--log ...] [--no-redirects] --out-csv OUT.csv
--bench-dir selects which awibench is imported (e.g. an origin/main checkout for 'before', this branch for 'after').
Output is deterministic: same inputs -> byte-identical CSV."""
import argparse, csv, hashlib, json, socket, sys, urllib.request
from pathlib import Path

def forbid_network():
    def _no(*a, **k): raise RuntimeError("rescore.py is offline: network use is forbidden")
    urllib.request.urlopen = _no
    socket.create_connection = _no
    socket.socket.connect = _no

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench-dir", required=True); ap.add_argument("--set", required=True); ap.add_argument("--set-file", required=True)
    ap.add_argument("--log", action="append", required=True, metavar="PROVIDER=PATH"); ap.add_argument("--no-redirects", action="store_true")
    ap.add_argument("--out-csv", required=True)
    a = ap.parse_args(argv)
    forbid_network()
    sys.path.insert(0, str(Path(a.bench_dir).resolve()))
    from awibench import checks
    try: from awibench import urlmatch
    except ImportError: urlmatch = None
    if urlmatch and a.no_redirects: urlmatch.load_redirects = lambda path=None: {}
    rows = {}
    for l in Path(a.set_file).read_text().splitlines():
        if l.strip(): r = json.loads(l); rows[r["id"]] = r
    out, tally, not_graded, inputs = [], {}, {}, [a.set_file]
    for spec in a.log:
        prov, _, path = spec.partition("="); inputs.append(path)
        for l in Path(path).read_text().splitlines():
            if not l.strip(): continue
            rec = json.loads(l)
            if rec.get("set") not in (None, a.set): continue
            row = rows.get(rec.get("item_id")); logged = rec.get("pass")
            if row is None: ctype = ""
            else: ctype = row["check"]["type"]
            res = rec.get("results") or []
            regraded = matched = rules = ""
            if row is not None and rec.get("status") in ("pass", "fail"):
                ok = checks.news_check(row, res)[0] if "alt_check" in row else checks.search_check(row["check"], res)[0]
                regraded = ok
                if urlmatch and ctype == "url_exact":
                    _, m, rl = urlmatch.match(row["check"]["value"], res)
                    matched, rules = m or "", ";".join(rl)
                t = tally.setdefault((prov, ctype), [0, 0]); t[0] += bool(ok); t[1] += 1
            else: not_graded[prov] = not_graded.get(prov, 0) + 1
            out.append([prov, path, rec.get("item_id"), ctype, logged, regraded, matched, rules])
    with open(a.out_csv, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["provider", "log_path", "item_id", "check_type", "logged_pass", "regraded_pass", "matched_url", "rule_ids"]); w.writerows(out)
    print(f"{'provider':12} {'check_type':18} passed/denominator")
    for (p, c), (n, d) in sorted(tally.items()): print(f"{p:12} {c:18} {n}/{d}")
    for p in sorted({p for p, _ in tally} | set(not_graded)):
        n = sum(v[0] for (q, _), v in tally.items() if q == p); d = sum(v[1] for (q, _), v in tally.items() if q == p)
        print(f"{p:12} {'TOTAL':18} {n}/{d}  not_graded={not_graded.get(p, 0)}")
    rp = Path(a.bench_dir) / "sets" / "url_redirects.json"
    if urlmatch and not a.no_redirects and rp.is_file(): inputs.append(str(rp))
    print("inputs sha256:")
    for p in inputs: print(f"  {sha(p)}  {p}")

if __name__ == "__main__": main()
