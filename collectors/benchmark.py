#!/usr/bin/env python3
"""Live benchmark entry point (GitHub Actions). Wires SPEC-bench (bench/awibench) into the hooks.

- load_test_set  -> bench/sets/* (validated; refuses to run on invalid sets)
- cost_per_1k    -> awibench.cost.cost_columns (entry + volume tier, D3)
- freshness_score-> awibench.freshness.freshness (news sets)
- run log        -> bench/runs/YYYY-MM-DD/<provider>/<set>.jsonl -> awibench.tables -> data/benchmarks/latest.json
Free tier only, hard $0 cap (D4). Providers without a key, adapter, known free quota or ToS clearance are skipped.
ADAPTERS is empty until signups are approved; there is no mock path here (mocks live in bench/tests).
"""
import argparse, datetime as dt, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT / "bench"))
from awibench import runner, tables, validate, cost, freshness
from awibench.util import load_yaml, read_jsonl, sha256_file

BENCH = ROOT / "bench"
ADAPTERS = {}   # {provider_id: {"search"|"scrape"|"browser": fn(key, item) -> dict}}; see bench/README.md for the contract

def load_test_set(name):
    f = BENCH / "sets" / (name if "." in name else f"{name}.jsonl")
    return load_yaml(f) if f.suffix == ".yaml" else read_jsonl(f)

cost_per_1k = cost.cost_columns
freshness_score = freshness.freshness

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--mode", choices=["smoke", "full"], default="smoke")
    ap.add_argument("--reprobe-offset", type=int, choices=freshness.REPROBE_OFFSETS_H); a = ap.parse_args()
    cfg = load_yaml(BENCH / "config.yaml"); provs = json.loads((ROOT / "collectors/providers.json").read_text())["providers"]
    pricing = load_yaml(BENCH / "pricing.yaml")["providers"]; tos = load_yaml(BENCH / "tos_matrix.yaml")["providers"]
    today = dt.date.today().isoformat(); sets = {}; skips = []; flags = []
    for name, (errs, warns) in validate.validate_by_set(BENCH / "sets", cfg).items():
        if errs:   # never skip silently: every skipped set is logged with its reason and shown on the site
            skips.append({"scope": "set", "set": name, "status": "skipped_invalid_set", "reason": "; ".join(errs[:3]) + (f" (+{len(errs) - 3} more)" if len(errs) > 3 else "")})
            print("SKIP set", name, "->", skips[-1]["reason"]); continue
        f = BENCH / "sets" / ("browser_tasks.yaml" if name == "browser_tasks" else f"{name}.jsonl")
        rows = load_test_set(f.name)
        entry = {"rows": rows[:10] if (a.mode == "smoke" and not name.startswith("news_")) else rows, "sha": sha256_file(f), "flags": []}
        if name.startswith("news_"):
            entry["flags"] = json.loads(f.with_suffix(".meta.json").read_text()).get("flags", [])
            for fl in entry["flags"]: flags.append({"set": name, "flag": fl}); print("FLAG", name, fl)
        sets[name] = entry
    news = sorted(k for k in sets if k.startswith("news_"))
    for old in news[:-1]: sets.pop(old)   # only the latest week is run
    only = None
    if a.reprobe_offset:   # §3: re-query only the misses of the latest news set at +6/+24/+72 h
        sets = {k: v for k, v in sets.items() if k.startswith("news_")}
        due = {}
        for name, sv in sets.items():
            for f in (BENCH / "runs").glob(f"*/*/{name}.jsonl"):
                probes = {}
                for r in read_jsonl(f):
                    if r["status"] in ("pass", "fail"): probes.setdefault(r["item_id"], []).append({"offset_h": r.get("offset_h", 0), "results": r.get("results", [])})
                due[f.parent.name] = set(freshness.misses_due(sv["rows"], probes, a.reprobe_offset))
        only = lambda pid, item_id: item_id in due.get(pid, set())
    s = runner.run(providers=provs, sets=sets, pricing=pricing, tos=tos, adapters=ADAPTERS, cfg=cfg, runs_dir=BENCH / "runs",
                   ledger_path=ROOT / "data" / "spend" / f"{today[:7]}-quota.json", run_date=today, env=os.environ,
                   offset_h=a.reprobe_offset or 0, only_ids=only)
    for p in s["providers"]: print(json.dumps(p))
    doc = tables.build(BENCH / "runs", pricing, BENCH / "sets", tos)
    for p in s["providers"]:
        if p["status"] not in ("ran",):
            skips.append({"scope": "provider", "provider": p["provider"], "category": p["category"], "status": p["status"], "reason": p.get("reason", p["status"])})
    if not sets: skips.append({"scope": "run", "status": "no_valid_sets", "reason": "no test set passed validation; no provider calls made"})
    doc["run_summary"] = s; doc["run_date"] = today; doc["sets_valid"] = sorted(sets); doc["skips"] = skips; doc["flags"] = flags
    (BENCH / "runs" / today).mkdir(parents=True, exist_ok=True)
    (BENCH / "runs" / today / f"_skips-offset{a.reprobe_offset or 0}.json").write_text(json.dumps({"skips": skips, "flags": flags}, indent=1))
    out = ROOT / "data" / "benchmarks" / "latest.json"; out.parent.mkdir(parents=True, exist_ok=True)
    probs = tables.untraced_cells(doc, BENCH)
    if probs: print("untraced cells:", probs); return 1
    out.write_text(json.dumps(doc, indent=1)); print("wrote", out, "has_results:", doc["has_results"]); return 0

if __name__ == "__main__": sys.exit(main())
