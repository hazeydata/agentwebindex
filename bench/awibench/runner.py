"""Benchmark runner: free-tier only, hard $0 cap (D4), quota halting (AC11), ToS skips (D2/AC12),
run log per §4: runs/YYYY-MM-DD/<provider>/<set>.jsonl.

Provider-level outcomes: skipped_no_key, not_implemented, skipped_no_free_tier, halted_quota, ran,
stale_auth_error, skipped_all_errors. A provider with no call that returned data writes no run lines and logs no spend.
stale_auth_error (CEO ruling 2026-10-19): any HTTP 401/403 stops the provider at the first one. Its row is NOT published:
tables.collect never uses a log holding an auth_error line, and collectors/benchmark.py keeps the previous latest.json row.
Every billed call stays in the ledger, including all calls before the 401/403 (no all-errors rollback).
With no data from any provider and no ledger change, the ledger and run summary are not written.
Line statuses: pass, fail, skipped_tos, auth_error (the 401/403 that stopped a provider; not scored).
Quota ledger (units in the provider's billing unit; browser = billed minutes) lives at
data/spend/YYYY-MM-quota.json for live runs; mock runs must use a dir outside PUBLISHED_DIRS.
"""
import datetime as dt, hashlib, json, math, os, re, time
from pathlib import Path
from .util import PUBLISHED_DIRS, is_under, not_captured
from . import checks, cost

SET_KIND = {"search_evergreen": "search", "scrape_urls": "scrape", "browser_tasks": "browser"}  # news_* -> search

def set_kind(name):
    if name.startswith("newsfetch_"): return "browser"   # browser fetch of the frozen news set's article URLs (awibench.newsfetch)
    return "search" if name.startswith("news_") else SET_KIND[name]

class CapViolation(RuntimeError): pass

class QuotaLedger:
    def __init__(self, path, month):
        self.path = Path(path); self.d = json.loads(self.path.read_text()) if self.path.exists() else {"month": month, "used": {}, "over_free_units": 0}
    def used(self, pool): return self.d["used"].get(pool, 0)
    def add(self, pool, units): self.d["used"][pool] = self.used(pool) + units
    def save(self): self.path.parent.mkdir(parents=True, exist_ok=True); self.path.write_text(json.dumps(self.d, indent=1, sort_keys=True))

def eligibility(pid, pricing, cfg):
    """Returns (ok, reason). Needs a known free quota, and no card requirement unless the CEO lists the provider."""
    ft = (pricing or {}).get("free_tier") or {}
    q = ft.get("units_per_month")
    if not_captured(q) or q <= 0: return False, "free tier quota NOT CAPTURED or zero"
    if ft.get("needs_card") is not False and pid not in cfg.get("ceo_free_tier_allowlist", []):
        return False, "free tier needs a card (or unknown) and provider not on CEO list"
    return True, "free tier"

def benchmark_forbidden(tos_entry):
    return ((tos_entry or {}).get("benchmarking") or {}).get("status") == "forbidden"

def anti_bot_allowed(tos_entry):
    t = tos_entry or {}
    return t.get("allowed") == "yes" and (t.get("anti_bot_scraping") or {}).get("status") != "forbidden"

def browser_units(raw_minutes, pid, pricing, cfg):
    """Browser billing in the provider's unit: per-connection blocks (config browser_unit_seconds) or billed minutes."""
    sec = (cfg.get("browser_unit_seconds") or {}).get(pid)
    if sec: return math.ceil(raw_minutes * 60 / sec - 1e-9)
    t = (pricing.get("tiers") or {}).get("entry") or {}
    return cost.billed_minutes(raw_minutes, 0 if not_captured(t.get("min_minutes")) else t["min_minutes"], 0 if not_captured(t.get("rounding_minutes")) else t["rounding_minutes"])

def estimate_units(pricing, kind, item, pid=None, cfg=None):
    m = pricing.get("model"); cpc = pricing.get("credits_per_call") or {}
    if kind == "browser" and (m == "per_hour_browser" or ((cfg or {}).get("browser_unit_seconds") or {}).get(pid)):
        return browser_units(item.get("timeout_s", 60) / 60, pid, pricing, cfg or {})
    if kind in cpc: return cpc[kind]          # e.g. Firecrawl search = 2 credits, under any model
    if m == "credits": return 1
    if m == "per_hour_browser":
        t = (pricing.get("tiers") or {}).get("entry") or {}
        mm = 0 if not_captured(t.get("min_minutes")) else t["min_minutes"]; rr = 0 if not_captured(t.get("rounding_minutes")) else t["rounding_minutes"]
        return cost.billed_minutes(item.get("timeout_s", 60) / 60, mm, rr)
    if m == "per_gb": return (pricing.get("est_gb_per_call") or {}).get(kind, 0.01)
    return 1

def _request(kind, item):
    return {"search": lambda: {"query": item["query"]}, "scrape": lambda: {"url": item["url"]},
            "browser": lambda: {"task": item["id"], "start_url": item["start_url"]}}[kind]()

def _grade(kind, set_name, item, out, cfg):
    if kind == "search":
        return checks.news_check(item, out.get("results", [])) if set_name.startswith("news_") else checks.search_check(item["check"], out.get("results", []))
    if kind == "scrape": return checks.scrape_check(item, out, cfg["challenge_signatures"])
    return checks.browser_check(item, out)

def run(*, providers, sets, pricing, tos, adapters, cfg, runs_dir, ledger_path, run_date, env, mock=False,
        offset_h=0, only_ids=None, clock=time.perf_counter, now_fn=None):
    """sets: {set_name: {"rows": [...], "sha": str}}. Returns summary dict. Raises CapViolation on any billed $ > 0."""
    if mock and (is_under(runs_dir, PUBLISHED_DIRS) or is_under(ledger_path, PUBLISHED_DIRS)):
        raise RuntimeError("mock run output must not go to published data/, dist/ or bench/runs/")
    cap = float(env.get("MONTHLY_CAP_USD", "0") or 0)
    if cap != 0 or cfg.get("monthly_cap_usd", 0) != 0:
        raise CapViolation("month one is free-tier only: MONTHLY_CAP_USD must be 0 (D4)")
    ledger = QuotaLedger(ledger_path, run_date[:7]); used_start = json.dumps(ledger.d, sort_keys=True); summary = {"run_date": run_date, "mock": mock, "offset_h": offset_h, "providers": []}
    for p in providers:
        pid = p["id"]; pp = pricing.get(pid, {})
        for kind in p["categories"]:
            pr = pp.get(kind) or {}
            base = {"provider": pid, "category": kind}
            t_e = tos.get(pid) or {}
            if benchmark_forbidden(t_e):
                summary["providers"].append(base | {"status": "excluded_tos_benchmarking", "reason": f"terms forbid benchmarking: {t_e['benchmarking'].get('quote')} ({t_e.get('terms_url')}, checked {t_e.get('checked_at')})"}); continue
            m1 = cfg.get("month1_free_tier_providers")
            if m1 is not None and pid not in m1:
                summary["providers"].append(base | {"status": "skipped_not_month1", "reason": "not on the month-one free-quota list (config month1_free_tier_providers)"}); continue
            key = env.get(p["key_env"])
            fn = (adapters.get(pid) or {}).get(kind)
            if not key: summary["providers"].append(base | {"status": "skipped_no_key", "reason": "not run (no key)"}); continue
            if fn is None: summary["providers"].append(base | {"status": "not_implemented"}); continue
            ok, why = eligibility(pid, pr, cfg)
            if not ok: summary["providers"].append(base | {"status": "skipped_no_free_tier", "reason": why}); continue
            pool = pr["free_tier"].get("pool") or pid; quota = pr["free_tier"]["units_per_month"]
            status = {"status": "ran", "calls": 0, "data_calls": 0}; used0 = ledger.d["used"].get(pool); pending = {}
            for set_name, s in sets.items():
                if set_kind(set_name) != kind: continue
                out_f = Path(runs_dir) / run_date / pid / f"{set_name}.jsonl"; fh = pending.setdefault(out_f, [])
                for item in s["rows"]:
                    if only_ids is not None and not (only_ids(pid, item["id"]) if callable(only_ids) else item["id"] in only_ids): continue
                    line = {"ts": (now_fn() if now_fn else dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds"), "provider": pid, "set": set_name,
                            "set_sha": s["sha"], "set_rows": len(s["rows"]), "set_flags": s.get("flags", []), "item_id": item["id"], "request": _request(kind, item), "offset_h": offset_h, "mock": mock}
                    if item.get("class") == "anti_bot" or item.get("category") == "anti_bot":
                        t = tos.get(pid) or {}
                        if not anti_bot_allowed(t):
                            ab = (t.get("anti_bot_scraping") or {})
                            fh.append(json.dumps(line | {"status": "skipped_tos", "pass": None, "billed_units": 0, "response_hash": None, "latency_ms": None,
                                                        "reason": f"tos_matrix: allowed={t.get('allowed')} anti_bot_scraping={ab.get('status')} checked_at={t.get('checked_at')} terms={t.get('terms_url')}"}) + "\n")
                            continue
                    est = estimate_units(pr, kind, item, pid, cfg)
                    wc = ((cfg.get("worst_case_units_per_call") or {}).get(pid) or {}).get(kind)
                    if wc is not None: est = max(est, wc)   # pre-call worst-case guard (unverified per-call cost)
                    if ledger.used(pool) + est > quota:
                        status = {"status": "halted_quota", "calls": status["calls"], "data_calls": status["data_calls"], "reason": f"next call ~{est} units would exceed free quota {quota} (used {ledger.used(pool)})"}
                        break
                    t0 = clock()
                    try: out = fn(key, item)
                    except Exception as e: out = {"error": f"{type(e).__name__}: {e}"}
                    lat = (clock() - t0) * 1000
                    auth = re.match(r"\S+: HTTP 40([13])\b", str(out.get("error", "")))   # bad or unauthorised key
                    # ASSUMPTION (unsourced): a rejected 401 is not billed, so 0 units. A 403 may mean quota/plan exhausted, so it is charged the estimate.
                    units = 0 if auth and auth.group(1) == "1" else out.get("billed_units", est)
                    if kind == "browser" and "billed_units" in out: units = browser_units(out["billed_units"], pid, pr, cfg)
                    ledger.add(pool, units); status["calls"] += 1; status["data_calls"] += "error" not in out
                    passed, reason = (False, out["error"]) if "error" in out else _grade(kind, set_name, item, out, cfg)
                    body = json.dumps({k: v for k, v in out.items() if k != "file_bytes"}, sort_keys=True, default=str)
                    rec = line | {"status": "pass" if passed else "fail", "pass": passed, "reason": reason, "latency_ms": round(lat, 3),
                                  "billed_units": units, "proxy_gb": out.get("proxy_gb", 0), "http_status": out.get("http_status"),
                                  "response_hash": hashlib.sha256(body.encode()).hexdigest()}
                    if kind == "search": rec["results"] = [{k: r.get(k) for k in ("url", "title", "published_at")} for r in out.get("results", [])[:10]]
                    if auth: rec |= {"status": "auth_error", "pass": None}   # recorded, never scored or counted as attempted
                    fh.append(json.dumps(rec) + "\n")
                    if auth:   # stop this provider now; its row is not published (previous row kept), its spend is kept
                        status = {"status": "stale_auth_error", "calls": status["calls"], "data_calls": status["data_calls"],
                                  "reason": f"HTTP 40{auth.group(1)} on {item['id']} ({set_name}); not published, previous row kept"}
                        break
                    if ledger.used(pool) > quota:
                        over = ledger.used(pool) - quota
                        ledger.d["over_free_units"] = ledger.d.get("over_free_units", 0) + over  # any unit past the free tier is billable
                        ledger.save(); raise CapViolation(f"{pid}: billed units exceeded free quota by {over}; $0 cap violated")
                if status["status"] in ("halted_quota", "stale_auth_error"): break
            if status["calls"] and not status["data_calls"]:   # every call errored: no run lines
                if status["status"] != "stale_auth_error":   # not run, no spend; an auth error keeps every billed unit
                    status = {"status": "skipped_all_errors", "calls": status["calls"], "data_calls": 0, "reason": "not run (every call errored)"}
                    if used0 is None: ledger.d["used"].pop(pool, None)
                    else: ledger.d["used"][pool] = used0
                elif used0 is None and not ledger.used(pool): ledger.d["used"].pop(pool, None)   # nothing billed: no empty entry
                pending = {}
            for out_f, lines in pending.items():
                if lines:
                    out_f.parent.mkdir(parents=True, exist_ok=True)
                    with open(out_f, "a") as fh: fh.write("".join(lines))
            summary["providers"].append(base | status | {"quota_used": ledger.used(pool), "quota": quota})
    if not any(p.get("data_calls") for p in summary["providers"]) and json.dumps(ledger.d, sort_keys=True) == used_start:
        return summary   # nothing returned data and nothing billed: leave ledger and runs untouched
    ledger.save()
    if ledger.d.get("over_free_units", 0) > 0: raise CapViolation("ledger shows usage past the free tier (billed $ > 0)")
    sp = Path(runs_dir) / run_date / f"_run-offset{offset_h}.json"; sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(summary, indent=1)); return summary
