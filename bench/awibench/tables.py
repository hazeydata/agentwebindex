"""§4 / AC8 / AC12 / AC14: published tables are generated ONLY from run log files.
Every numeric cell is {"v": number, "src": [{"file": rel_path, "lines": [n, ...]}], ...}.

python -m awibench.tables RUNS_DIR OUT_JSON [--pricing PRICING] [--sets SETS_DIR]
"""
import argparse, datetime as dt, json, statistics, sys
from collections import defaultdict
from pathlib import Path
from .util import BENCH, load_yaml, read_jsonl, not_captured
from . import cost, freshness, checks
from .gates import vendor_confirmed, publishable
from .runner import set_kind, is_rate_limited, quota_degraded, forbidden_clause

def _cell(v, refs, **extra): return {"v": v, "src": refs, **extra} if v is not None else {"v": None, "src": [], **extra}

def _refs(rel, lines): return [{"file": rel, "lines": sorted(lines)}] if lines else []

def is_timeout(r):
    """A fail whose reason is a timeout (the provider's own 'Timed out.' or our client read timeout). Still a fail; counted for display."""
    return r.get("status") == "fail" and any(k in str(r.get("reason") or "") for k in ("Timed out", "TimeoutError", "timed out"))

PARTIAL_STATUSES = ("pass", "fail", "skipped_tos")

def is_partial(main):
    """Fewer set items reached than the set holds. Only pass/fail/skipped_tos lines count as reached."""
    reached = {r.get("item_id") for _, r in main if r.get("status") in PARTIAL_STATUSES}
    return bool(main) and len(reached) < (main[0][1].get("set_rows") or 0)

def week_labels(n, cfg=None):
    """Owner ruling 1 / AC3: labels from the real row count (not from free text)."""
    from .newsgen import week_flags
    nw = (cfg or {}).get("news") or {}
    return week_flags(n or 0, {"total": nw.get("total", 50), "min_total": nw.get("min_total", 25)})

def not_run_yet(summary, cfg, doc):
    """Owner ruling 3: month-one providers with no table row (no adapter/set yet) show as 'not run yet'.
    A provider that ran (status ran, stale_<reason> or stopped_<reason>) is never 'not run yet', even when collect dropped its log."""
    shown = {r["provider"] for t in doc["tables"].values() for r in t}; out = []
    shown |= {p["provider"] for p in (summary or {}).get("providers", []) if p["status"] == "ran" or p["status"].startswith(("stale_", "stopped_"))}
    for p in (summary or {}).get("providers", []):
        pid = p["provider"]
        if pid in (cfg.get("month1_free_tier_providers") or []) and pid not in shown and pid not in {o["provider"] for o in out}:
            cats = sorted({q["category"] for q in summary["providers"] if q["provider"] == pid})
            out.append({"provider": pid, "categories": cats, "status": "not run yet", "reason": p.get("reason") or p["status"]})
    return out

def collect(runs_dir):
    """Latest dated log per (provider, set). Returns {(pid,set): (rel, [(lineno, rec)])}.
    CEO ruling 2026-10-19: a log that did not cover the full set (fewer pass/fail/skipped_tos items at offset 0 than set_rows,
    any auth_error line, any line the runner stamped run_status=stale_<reason> or stopped_<reason>, or more than 20% of calls answered HTTP 429 (CEO rule 3, also for old logs)) is never used; the earlier complete log stays in force."""
    runs_dir = Path(runs_dir); out = {}
    for f in sorted(runs_dir.glob("*/*/*.jsonl")):
        rel = str(f.relative_to(runs_dir.parent)); key = (f.parent.name, f.stem)
        lines = [(i, r) for i, r in enumerate(read_jsonl(f), 1)]
        if any(r.get("status") == "auth_error" or r.get("run_status") not in (None, "ran") for _, r in lines) or is_partial([x for x in lines if x[1].get("offset_h", 0) == 0]) or quota_degraded([r for _, r in lines if r.get("offset_h", 0) == 0]): continue
        out[key] = (rel, lines)   # sorted() => later dates win
    return out

def exclusions(tos):
    return [{"provider": pid, "reason": f"terms forbid {c.replace('_', ' ')}", "quote": t[c].get("quote"), "terms_url": t.get("terms_url"),
             "checked_at": t.get("checked_at"), "note": t.get("note")} for pid, t in sorted((tos or {}).items()) if vendor_confirmed(t) and (c := forbidden_clause(t))]   # R10: a gated, unconfirmed provider is not named anywhere in published output

def publish_gate(pid, pricing, tos):
    """AWI-PROVIDER-CONTEXTDEV-001 AC23. (ok, reason); both gates must pass.
    P0: every category in pricing.yaml has a numeric entry price plus a source_url and checked_at that are not NOT CAPTURED.
    P1: tos_matrix has full_read true, verdict 'allowed', allowed 'yes', and neither benchmarking nor publishing_results forbidden."""
    pe = (pricing or {}).get(pid) or {}; cats = [c for c, v in pe.items() if isinstance(v, dict) and "tiers" in v]
    def real(v): return isinstance(v, str) and v.strip() != "" and not not_captured(v)
    p0 = bool(cats) and all(cost.billed_usd(pe[c], "entry", []) is not None and real(pe[c].get("source_url")) and real(pe[c].get("checked_at")) for c in cats)
    t = (tos or {}).get(pid) or {}
    p1 = t.get("full_read") is True and t.get("verdict") == "allowed" and t.get("allowed") == "yes" and forbidden_clause(t) is None
    return p0 and p1, f"P0 pricing {'passed' if p0 else 'not passed'}; P1 terms {'passed' if p1 else 'not passed'}"

def held_providers(cfg, pricing, tos):
    """Providers kept out of every published table: config publish_hold (this build) plus any publish_gate provider whose P0/P1 + R10 gate is not passed, plus any tos_matrix provider whose vendor confirmation is required and missing."""
    held = set((cfg or {}).get("publish_hold") or [])
    held |= {pid for pid in ((cfg or {}).get("publish_gate") or []) if not publishable(pid, pricing, tos)}
    return held | {pid for pid, t in (tos or {}).items() if not vendor_confirmed(t)}   # R10: every gated provider without a confirmation record

def strip_held(doc, held):
    """Remove held providers from every published section of a latest.json doc (run logs under bench/runs keep them)."""
    if not held: return doc
    for kind in doc["tables"]: doc["tables"][kind] = [r for r in doc["tables"][kind] if r["provider"] not in held]
    for k in ("freshness", "not_run_yet", "skips", "freshness_notes"):
        if k in doc: doc[k] = [r for r in doc[k] if r.get("provider") not in held]
    if isinstance(doc.get("run_summary"), dict):
        doc["run_summary"] = doc["run_summary"] | {"providers": [p for p in doc["run_summary"]["providers"] if p["provider"] not in held]}
    doc["has_results"] = any(r["attempted"]["v"] for t in doc["tables"].values() for r in t)
    return doc

def also_priced_providers(pricing, latest, providers):
    """Names of providers in pricing.yaml with no published row in latest.json (the site's untested_providers rule), minus providers whose
    providers.json entry has exclude_from_also_priced true (R3, AWI-PROVIDER-CONTEXTDEV-001; absent = false). Order follows pricing.yaml."""
    published = {r["provider"] for t in ((latest or {}).get("tables") or {}).values() for r in t}
    meta = {p["id"]: p for p in providers}
    return [meta.get(pid, {}).get("name", pid) for pid in pricing if pid not in published and meta.get(pid, {}).get("exclude_from_also_priced") is not True]

GRADED_REASONS = {"url_exact in top10", "url_exact not in top10", "domain in top5", "domain not in top5", "snippet match", "snippet not found in top5"}

def regrade(set_name, lines, sets_dir):
    """CEO ruling 2026-10-20: search lines are re-graded from their stored results with the current grader; the log stays raw."""
    f = Path(sets_dir) / f"{set_name}.jsonl" if sets_dir else None
    if set_kind(set_name) != "search" or not f or not f.exists(): return lines, None
    rows = {r["id"]: r for r in read_jsonl(f)}; out = []
    for n, r in lines:
        item = rows.get(r.get("item_id"))
        if r.get("status") in ("pass", "fail") and "results" in r and item and r.get("reason") in GRADED_REASONS:
            ok, why = checks.news_check(item, r["results"]) if set_name.startswith("news_") else checks.search_check(item["check"], r["results"])
            r = r | {"status": "pass" if ok else "fail", "pass": ok, "reason": why}
        out.append((n, r))
    return out, checks.GRADER_VERSION

def build(runs_dir, pricing, sets_dir=None, tos=None, cfg=None):
    logs = collect(runs_dir); doc = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                     "source": "run logs only (SPEC-bench §4)", "tables": {"search": [], "scrape": [], "browser": []}, "freshness": []}
    doc["exclusions"] = exclusions(tos); excluded = {e["provider"] for e in doc["exclusions"]} | held_providers(cfg, pricing, tos)   # excluded + held: no row
    for (pid, set_name), (rel, lines) in sorted(logs.items()):
        if pid in excluded: continue          # never shown in benchmark tables, even if a stray log exists
        kind = set_kind(set_name); lines, gv = regrade(set_name, lines, sets_dir)
        main = [(n, r) for n, r in lines if r.get("offset_h", 0) == 0]
        rl = [n for n, r in main if is_rate_limited(r)]
        scored = [(n, r) for n, r in main if r["status"] in ("pass", "fail") and n not in rl]
        tos = [(n, r) for n, r in main if r["status"] == "skipped_tos"]
        passn = [n for n, r in scored if r["status"] == "pass"]; alln = [n for n, _ in scored]
        tmo = [n for n, r in scored if is_timeout(r)]
        lat = [r["latency_ms"] for _, r in scored]
        pr = (pricing.get(pid) or {}).get(kind)
        cl = [r for n, r in main if n not in rl]   # 429s are outside every denominator, cost included
        ver = ((pr or {}).get("credits_per_call_verified") or {})
        if isinstance(ver.get(kind), (int, float)):   # documented units per call replace the runner's unverified guard in the log
            cl = [r | {"billed_units": ver[kind]} for r in cl]
        cc = cost.cost_columns(pr, cl)
        pref = [{"file": "bench/pricing.yaml", "provider": pid, "category": kind}]
        run_date = Path(rel).parts[-3]   # runs/<YYYY-MM-DD>/<provider>/<set>.jsonl
        row = {"provider": pid, "set": set_name, "run_date": run_date, "set_sha": main[0][1]["set_sha"] if main else None, "set_rows": main[0][1].get("set_rows") if main else None,
               "flags": (week_labels(main[0][1].get("set_rows"), cfg) if set_name.startswith("news_") else main[0][1].get("set_flags", [])) if main else [], "mock": any(r.get("mock") for _, r in lines),
               "partial": is_partial(main), "grader_version": gv or "run-log",
               "attempted": _cell(len(alln) or None, _refs(rel, alln)),
               "passed": _cell(len(passn) if alln else None, _refs(rel, alln)),
               "failed": _cell(len(alln) - len(passn) if alln else None, _refs(rel, alln)),
               "rate_limited": _cell(len(rl) or None, _refs(rel, rl), label="rate-limited (free tier)",
                                     note="HTTP 429 from the provider; the free-tier rate limit is the cause. Excluded from the pass-rate denominator, not a quality fail." if rl else None),
               "timed_out": _cell(len(tmo) or None, _refs(rel, tmo), note="counted as fails: the request timed out (provider-side 'Timed out.' or no reply within the client limit)" if tmo else None),
               "answered_display": f"{len(passn)}/{len(alln)} answered" + (f" ({len(tmo)} of the fails timed out)" if tmo else "") + (f" + {len(rl)} rate-limited (free tier)" if rl else ""),
               "skipped_tos": {"count": len(tos), "src": _refs(rel, [n for n, _ in tos]), "tos_reason": tos[0][1]["reason"] if tos else None},
               "success_rate": _cell(len(passn) / len(alln) if alln else None, _refs(rel, alln)),
               "p50_latency_ms": _cell(statistics.median(lat) if lat else None, _refs(rel, alln)),
               "free_quota_note": cc["free_quota_note"],
               "cost_units_note": (f"cost uses {ver[kind]} credit(s) per call as documented at {ver.get('source_url')} (checked {ver.get('checked_at')}); "
                                   "the run log's billed_units held the runner's unverified guard") if isinstance(ver.get(kind), (int, float)) else None}
        note = (((cfg or {}).get("run_notes") or {}).get(pid) or {}).get(run_date, {}).get(kind)
        if note: row["run_note"] = note   # owner-set label, e.g. a preliminary retest (config run_notes)
        for k in ("entry", "volume"):
            for col in (f"{k}_tier_cost_per_1k", f"{k}_tier_cost_per_successful_1k"):
                v = cc[col]; row[col] = _cell(None if v is None else ("inf" if v == float("inf") else v), _refs(rel, alln) + pref if v is not None else [],
                                               tier=cc[f"{k}_tier_name"], lower_bound=cc[f"{k}_tier_lower_bound"],
                                               lower_bound_reason="vendor minimum/rounding not on pricing page; billed time could be higher" if cc[f"{k}_tier_lower_bound"] else None,
                                               na_reason=cc[f"{k}_tier_na_reason"],
                                               display=cc[f"{k}_tier_na_reason"] if v is None and cc[f"{k}_tier_na_reason"] else cost.fmt(v, cc[f"{k}_tier_lower_bound"]))
        doc["tables"][kind].append(row)
        if set_name.startswith("news_") and sets_dir and (Path(sets_dir) / f"{set_name}.jsonl").exists():
            rows = read_jsonl(Path(sets_dir) / f"{set_name}.jsonl")
            probes = defaultdict(list); used = []
            for n, r in lines:
                if r["status"] in ("pass", "fail") and n not in rl: probes[r["item_id"]].append({"offset_h": r.get("offset_h", 0), "results": r.get("results", [])}); used.append(n)
            run_at = min(dt.datetime.fromisoformat(r["ts"]) for _, r in main) if main else None
            mp = Path(sets_dir) / f"{set_name}.meta.json"
            frozen = json.loads(mp.read_text()).get("run_at", "")[:10] if mp.exists() else None
            if run_at and used and frozen and run_date != frozen:   # lag vs published_at is only comparable for runs on the freeze date
                doc.setdefault("freshness_notes", []).append({"provider": pid, "set": set_name, "run_date": run_date,
                    "note": f"no freshness row: ran {run_date}, after the set froze on {frozen}, so its lag is not comparable"}); used = []
            if run_at and used:
                fr = freshness.freshness(rows, probes, run_at, set_name.removeprefix("news_"), main[0][1]["set_sha"])
                doc["freshness"].append({"provider": pid, "set": set_name, "run_date": run_date, "flags": week_labels(main[0][1].get("set_rows"), cfg), "n": fr["n"], "week": fr["week"], "set_sha": fr["set_sha"],
                                         **{k: _cell(fr[k], _refs(rel, used)) for k in ("fresh_hit_at_10", "median_lag_h", "censored_share")}})
    doc["grader_version"] = checks.GRADER_VERSION
    doc["has_results"] = any(r["attempted"]["v"] for t in doc["tables"].values() for r in t)
    return doc

def untraced_cells(doc, root):
    """AC8 check: every numeric cell must cite existing run-log lines. Returns list of problems."""
    probs = []; root = Path(root)
    def walk(o, path):
        if isinstance(o, dict):
            if "v" in o and "src" in o:
                if isinstance(o["v"], (int, float, str)) and o["v"] is not None and o["v"] != "n/a":
                    logrefs = [s for s in o["src"] if s["file"].endswith(".jsonl")]
                    if not logrefs: probs.append(f"{path}: value {o['v']} has no run-log source")
                    for s in logrefs:
                        f = root / s["file"]
                        if not f.exists(): probs.append(f"{path}: missing {s['file']}"); continue
                        n = len([l for l in f.read_text().splitlines() if l.strip()])
                        if any(l < 1 or l > n for l in s["lines"]): probs.append(f"{path}: bad line ref in {s['file']}")
            for k, v in o.items(): walk(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o): walk(v, f"{path}[{i}]")
    walk(doc, "$"); return probs

def load_pricing(path):
    d = load_yaml(path); return d.get("providers", {})

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("runs_dir"); ap.add_argument("out")
    ap.add_argument("--pricing", default=str(BENCH / "pricing.yaml")); ap.add_argument("--sets", default=str(BENCH / "sets"))
    a = ap.parse_args(argv)
    tosp = BENCH / "tos_matrix.yaml"
    doc = build(a.runs_dir, load_pricing(a.pricing), a.sets, load_yaml(tosp)["providers"] if tosp.exists() else {}, load_yaml(BENCH / "config.yaml"))
    probs = untraced_cells(doc, Path(a.runs_dir).parent)
    if probs: print("\n".join(probs)); return 1
    from .util import PUBLISHED_DIRS, is_under
    if any(r["mock"] for t in doc["tables"].values() for r in t) and is_under(a.out, PUBLISHED_DIRS):
        print("refusing to publish mock results into", a.out); return 2
    Path(a.out).parent.mkdir(parents=True, exist_ok=True); Path(a.out).write_text(json.dumps(doc, indent=1))
    print("wrote", a.out, "has_results:", doc["has_results"]); return 0

if __name__ == "__main__": sys.exit(main())
