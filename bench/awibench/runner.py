"""Benchmark runner: free-tier only, hard $0 cap (D4), quota halting (AC11), ToS skips (D2/AC12),
run log per §4: runs/YYYY-MM-DD/<provider>/<set>.jsonl.

Provider-level outcomes: skipped_no_key, skipped_no_vendor_confirmation (contextdev/keenable live gate, R9/R22), not_implemented, skipped_no_free_tier, ran, and stale_<reason>.
Keenable (AWI-PROVIDER-KEENABLE-001): stopped_paid_usage (R1/AC20: HTTP 402, a usage block, billed units > 0, or paid_usage_allowed not false;
no further call) and rate_limited (AC19: a 429 without Retry-After, or a second 429 after the one retry, or the client-side limiter's hourly
cap). Both are, like stale_<reason>, never published; their run-log lines are stamped run_status. A keyless provider (guard.KEYLESS)
needs no env key. Keenable run-log lines carry no raw result URLs or snippets (AC23): only a count and the response hash.
CEO ruling 2026-10-19: a row publishes ONLY when the run covered the full set (items reached == set size). Any incomplete
run is stale_<reason>, reason one of STALE_REASONS:
  quota      - the free-quota guard stopped it before the next call
  timeout    - the provider's run budget (config run_budget_s) ran out
  auth_error - any HTTP 401/403 stops the provider at the first one
  budget     - the paid spend guard (config spend_budget) stopped it before the next request
  error      - anything else: every call errored, or fewer items reached than the set holds
A stale provider is not published: tables.collect never uses an incomplete log, and collectors/benchmark.py keeps the previous
latest.json row (or omits the provider entirely when it has none). Billed spend is always logged (no rollback).
The run log of a stale run is still written; only a provider with no data call and nothing billed writes no lines.
With no data from any provider and no ledger change, the ledger and run summary are not written.
Line statuses: pass, fail, skipped_tos, auth_error (the 401/403 that stopped a provider; not scored),
unbilled_no_response (no HTTP status line arrived; 0 units, not scored; CEO billing ruling 2026-10-19, status-line rule),
quota_stop (contextdev HTTP 402/401 USAGE_EXCEEDED; 0 units, not scored, stops the provider as stale_quota).
Quota ledger (units in the provider's billing unit; browser = billed minutes) lives at
data/spend/YYYY-MM-quota.json for live runs; mock runs must use a dir outside PUBLISHED_DIRS.
"""
import datetime as dt, fcntl, hashlib, json, math, os, re, time
from pathlib import Path
from .util import PUBLISHED_DIRS, is_under, not_captured
from . import checks, cost, guard, gates

SET_KIND = {"search_evergreen": "search", "scrape_urls": "scrape", "browser_tasks": "browser"}  # news_* -> search

def set_kind(name):
    if name.startswith("newsfetch_"): return "browser"   # browser fetch of the frozen news set's article URLs (awibench.newsfetch)
    return "search" if name.startswith("news_") else SET_KIND[name]

STALE_REASONS = ("quota", "timeout", "auth_error", "error", "budget")
REACHED = ("pass", "fail", "skipped_tos")   # line statuses that count as a reached set item

_HTTP_ANSWER = re.compile(r"\bHTTP [1-5]\d\d\b")
_NET_TEXT = re.compile(r"(URLError|gaierror|Connection(Refused|Reset|Aborted)?Error|RemoteDisconnected|TimeoutError|timeout): ")

def is_network_error(e):
    """True only when no HTTP status line ever arrived (CEO billing ruling 2026-10-19); a 401 is billed 0 separately (401 = 0 ASSUMPTION, accepted by the CEO)."""
    import socket, urllib.error
    if isinstance(e, urllib.error.HTTPError) or isinstance(getattr(e, "reason", None), urllib.error.HTTPError) or _HTTP_ANSWER.search(str(e)): return False
    if isinstance(e, (urllib.error.URLError, ConnectionError, TimeoutError, socket.timeout, socket.gaierror)): return True
    return isinstance(e, RuntimeError) and bool(_NET_TEXT.match(str(e)))

def _http_code(e):
    import urllib.error
    h = e if isinstance(e, urllib.error.HTTPError) else getattr(e, "reason", None)
    return f"HTTP {h.code}: " if isinstance(h, urllib.error.HTTPError) else ""

def is_rate_limited(r):
    """Owner ruling 2: any provider's HTTP 429 is a rate_limited outcome, not a quality fail; excluded from the pass-rate denominator."""
    return r.get("status") in ("fail", "rate_limited") and (r.get("http_status") == 429 or "HTTP 429" in str(r.get("reason") or ""))

def quota_degraded(recs):
    """CEO rule 3 (2026-10-19): more than 20% of a provider's calls answered HTTP 429; exactly 20% still publishes."""
    calls = [r for r in recs if r.get("status") != "skipped_tos"]
    return 5 * sum(map(is_rate_limited, calls)) > len(calls)

def stale(reason, status, why):
    assert reason in STALE_REASONS, reason
    return {"status": f"stale_{reason}", "calls": status["calls"], "data_calls": status["data_calls"], "reason": f"{why}; not published"}

class CapViolation(RuntimeError): pass

class QuotaLedger:
    def __init__(self, path, month):
        self.path = Path(path); self.d = json.loads(self.path.read_text()) if self.path.exists() else {"month": month, "used": {}, "over_free_units": 0}
    def used(self, pool): return self.d["used"].get(pool, 0)
    def add(self, pool, units): self.d["used"][pool] = self.used(pool) + units
    def save(self): self.path.parent.mkdir(parents=True, exist_ok=True); self.path.write_text(json.dumps(self.d, indent=1, sort_keys=True))

class SpendError(RuntimeError): pass

def _usd_ok(v): return type(v) in (int, float) and math.isfinite(v) and v >= 0

def _count_ok(v): return type(v) is int and v >= 0

def valid_spend(d, pid, month):
    return (isinstance(d, dict) and d.get("provider") == pid and d.get("month") == month and _count_ok(d.get("requests")) and _usd_ok(d.get("est_usd"))
            and ("rolled_over_to" not in d or (isinstance(d["rolled_over_to"], str) and re.fullmatch(r"\d{4}-\d{2}", d["rolled_over_to"]) is not None and d["rolled_over_to"] > month)))

class SpendFile:
    def __init__(self, spend_dir, pid, month, usd_per_1k):
        d = Path(spend_dir)
        if not d.is_dir() or not os.access(d, os.R_OK | os.W_OK | os.X_OK): raise SpendError(f"spend dir {d.name}/ missing or unreadable; failing closed")
        self.path = d / f"{pid}-{month}.json"; self.rate = usd_per_1k; self.lock = None
        try:
            self.lock = open(d / f".{pid}.lock", "a"); fcntl.flock(self.lock, fcntl.LOCK_EX)
        except OSError as e: self.release(); raise SpendError(f"cannot lock {self.path.name}: {type(e).__name__}; failing closed") from None
        try:
            if not self.path.exists():
                why = self._seed_refusal(d, pid, month)
                if why: self.release(); raise SpendError(f"spend file {self.path.name} missing: {why}; failing closed (rule 6)")
                self.d = {"provider": pid, "month": month, "usd_per_1k": usd_per_1k, "requests": 0, "est_usd": 0.0}
                self._write(self.path, self.d)
            else:
                self.d = json.loads(self.path.read_text())
                if not valid_spend(self.d, pid, month): raise ValueError("bad fields")
        except (OSError, ValueError) as e:
            self.release(); raise SpendError(f"spend file {self.path.name} empty, corrupt or unreadable ({type(e).__name__}); failing closed") from None
    @staticmethod
    def _write(path, d):
        tmp = path.with_name(path.name + ".tmp"); tmp.write_text(json.dumps(d, indent=1, sort_keys=True)); os.replace(tmp, path)
    def _seed_refusal(self, d, pid, month):
        months = sorted(f.stem[len(pid) + 1:] for f in d.glob(f"{pid}-*.json") if re.fullmatch(r"\d{4}-\d{2}", f.stem[len(pid) + 1:]))
        if not months: return None
        if months[-1] > month: return f"a later month file exists ({months[-1]})"
        y, m = map(int, month.split("-")); prev_m = f"{y - (m == 1)}-{12 if m == 1 else m - 1:02d}"
        prev = d / f"{pid}-{prev_m}.json"
        if not prev.exists(): return f"other spend files exist but the previous month {prev_m} is absent (gap or deletion)"
        try:
            pd = json.loads(prev.read_text())
            if not valid_spend(pd, pid, prev_m): raise ValueError("bad fields")
        except (OSError, ValueError): return f"previous month file {prev.name} is unreadable, corrupt or invalid"
        if pd.get("rolled_over_to") == month: return f"{prev.name} already rolled over to {month}, so this month's file was deleted"
        if "rolled_over_to" in pd: return f"{prev.name} rolled over to {pd['rolled_over_to']}, not {month}"
        self._write(prev, pd | {"rolled_over_to": month})
        return None
    def mtd(self): return self.d["est_usd"]
    def next_usd(self, units): return round(self.mtd() + units * self.rate / 1000, 6)
    def add(self, units):
        self.d["requests"] += units; self.d["est_usd"] = self.next_usd(units)
        self._write(self.path, self.d)
    def release(self):
        if self.lock: fcntl.flock(self.lock, fcntl.LOCK_UN); self.lock.close(); self.lock = None

def spend_month(ts):
    return dt.datetime.fromisoformat(ts).astimezone(dt.timezone.utc).strftime("%Y-%m")

def budget_blocked(sf, units, stop_usd):
    return sf.mtd() >= stop_usd or sf.next_usd(units) > stop_usd

def eligibility(pid, pricing, cfg):
    """Returns (ok, reason). Needs a known free quota, and no card requirement unless the CEO lists the provider."""
    ft = (pricing or {}).get("free_tier") or {}
    q = ft.get("units_per_month")
    if not_captured(q) or q <= 0: return False, "free tier quota NOT CAPTURED or zero"
    if ft.get("needs_card") is not False and pid not in cfg.get("ceo_free_tier_allowlist", []):
        return False, "free tier needs a card (or unknown) and provider not on CEO list"
    return True, "free tier"

def read_meter(meters, pid):
    try: v = (meters or {})[pid]()
    except Exception: return None
    return v if _usd_ok(v) else None

def meter_gap(meters, pid, m0, ledger_usd, spend_dir, run_date):
    mv = read_meter(meters, pid)
    if mv is None: return {"meter_usd": None, "ledger_usd": round(ledger_usd, 4), "meter_minus_ledger_usd": None}
    d = {"meter_usd": round(mv, 4), "ledger_usd": round(ledger_usd, 4), "meter_minus_ledger_usd": round(mv - ledger_usd, 4),
         "run_meter_usd": round(mv - m0[0], 4), "run_ledger_usd": round(ledger_usd - m0[1], 4), "run_meter_minus_ledger_usd": round((mv - m0[0]) - (ledger_usd - m0[1]), 4)}
    f = Path(spend_dir) / f"{pid}-meter-{run_date[:7]}.json"; f.parent.mkdir(parents=True, exist_ok=True)
    log = json.loads(f.read_text()) if f.exists() else {"provider": pid, "month": run_date[:7], "runs": []}
    log["runs"].append({"run_date": run_date} | d); f.write_text(json.dumps(log, indent=1, sort_keys=True))
    return d

def forbidden_clause(tos_entry):
    """'benchmarking' or 'publishing_results' when the terms forbid it (AWI-PROVIDER-CONTEXTDEV-001 AC5), else None."""
    t = tos_entry or {}
    return next((c for c in ("benchmarking", "publishing_results") if (t.get(c) or {}).get("status") == "forbidden"), None)

def benchmark_forbidden(tos_entry): return forbidden_clause(tos_entry) is not None

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
    if kind == "scrape": return checks.scrape_check(item, out, cfg["challenge_signatures"], cfg.get("challenge_html_signatures", ()))
    return checks.browser_check(item, out)

def run(*, providers, sets, pricing, tos, adapters, cfg, runs_dir, ledger_path, run_date, env, mock=False,
        offset_h=0, only_ids=None, clock=time.perf_counter, now_fn=None, spend_dir=None, meters=None, guard_dir=None, sleep=time.sleep):
    """sets: {set_name: {"rows": [...], "sha": str}}. Returns summary dict. Raises CapViolation on any billed $ > 0."""
    if mock and (is_under(runs_dir, PUBLISHED_DIRS) or is_under(ledger_path, PUBLISHED_DIRS)):
        raise RuntimeError("mock run output must not go to published data/, dist/ or bench/runs/")
    cap = float(env.get("MONTHLY_CAP_USD", "0") or 0)
    if cap != 0 or cfg.get("monthly_cap_usd", 0) != 0:
        raise CapViolation("month one is free-tier only: MONTHLY_CAP_USD must be 0 (D4)")
    spend_dir = Path(spend_dir) if spend_dir else Path(runs_dir).parent / "spend"
    if mock and is_under(spend_dir, PUBLISHED_DIRS + [Path(__file__).resolve().parent.parent / "spend"]):
        raise RuntimeError("mock run spend files must not go to bench/spend or published dirs")
    gdir = Path(guard_dir) if guard_dir else spend_dir   # credit-guard counters (config free_tier_guard.*.counter_file); live runs: data/spend
    if mock and is_under(gdir, PUBLISHED_DIRS + [Path(__file__).resolve().parent.parent / "spend"]):
        raise RuntimeError("mock run credit counters must not go to bench/spend or published dirs")
    ledger = QuotaLedger(ledger_path, run_date[:7]); used_start = json.dumps(ledger.d, sort_keys=True); summary = {"run_date": run_date, "mock": mock, "offset_h": offset_h, "providers": []}
    for p in providers:
        pid = p["id"]; pp = pricing.get(pid, {})
        for kind in p["categories"]:
            pr = pp.get(kind) or {}
            base = {"provider": pid, "category": kind}
            t_e = tos.get(pid) or {}
            if not gates.vendor_confirmed(t_e):   # R10: gated and unconfirmed -> never called, whatever the key/config say
                summary["providers"].append(base | {"status": "skipped_no_vendor_confirmation", "reason": "tos_matrix vendor confirmation required and not recorded; no call made"}); continue
            fc = forbidden_clause(t_e)
            if fc:
                summary["providers"].append(base | {"status": "excluded_tos_benchmarking", "reason": f"terms forbid {fc.replace('_', ' ')}: {t_e[fc].get('quote')} ({t_e.get('terms_url')}, checked {t_e.get('checked_at')})"}); continue
            m1 = cfg.get("month1_free_tier_providers")
            if m1 is not None and pid not in m1:
                summary["providers"].append(base | {"status": "skipped_not_month1", "reason": "not on the month-one free-quota list (config month1_free_tier_providers)"}); continue
            fg = (cfg.get("free_tier_guard") or {}).get(pid)
            if fg and pid in guard.FREE_ONLY and fg.get("paid_usage_allowed") is not False:   # Keenable R1: missing or non-bool fails closed
                summary["providers"].append(base | {"status": "stopped_paid_usage", "reason": f"config free_tier_guard.{pid}.paid_usage_allowed must be bool false; no call made"}); continue
            # R9/R26 (C07): a live-gated provider (guard.LIVE_GATED or guard.KEYLESS (Keenable R5: the keyless placeholder never bypasses it), or any tos entry that requires vendor confirmation, or any guard block
            # with a live_run_allowed key) is called only if guard.live_allowed() is True. A missing key, null record or missing block fails closed.
            if (pid in guard.LIVE_GATED or pid in guard.KEYLESS or t_e.get(gates.REQUIRED_FLAG) is True or (fg and "live_run_allowed" in fg)) and not guard.live_allowed(cfg, tos, pid):
                summary["providers"].append(base | {"status": "skipped_no_vendor_confirmation", "reason": f"live_run_allowed is not true or vendor_confirmation is not recorded (config free_tier_guard.{pid}); no call made"}); continue
            if pid in guard.LIVE_GATED and not fg: raise CapViolation(f"{pid}: free_tier_guard.{pid} missing; the credit guard must always apply")   # unreachable: live_allowed needs the block
            key = "keyless" if pid in guard.KEYLESS else env.get(p["key_env"])   # keenable: keyless public endpoint (AC18)
            fn = (adapters.get(pid) or {}).get(kind)
            if not key: summary["providers"].append(base | {"status": "skipped_no_key", "reason": "not run (no key)"}); continue
            if fn is None: summary["providers"].append(base | {"status": "not_implemented"}); continue
            ok, why = eligibility(pid, pr, cfg)
            if not ok: summary["providers"].append(base | {"status": "skipped_no_free_tier", "reason": why}); continue
            pool = pr["free_tier"].get("pool") or pid; quota = pr["free_tier"]["units_per_month"]
            sem = None; gmeta = {}; last_read = None
            if fg:   # AWI-PROVIDER-CONTEXTDEV-001: attempts guard per grant-anchor window, max(meter, counter), max_concurrent semaphore; free tier only
                if fg.get("paid_tier") is not False: raise CapViolation(f"{pid}: free_tier_guard.paid_tier must be false (no paid path)")
                quota = min(quota, fg["quota_credits"]); sem = guard.make_semaphore(fg["max_concurrent"])
            lim = guard.make_limiter(fg, clock, sleep) if fg else None   # keenable AC19: <= 9/s and <= 899 per rolling hour, client side
            # keenable AC23 + Keenable R5 (3): raw results count as publishable only when publish_raw_results is exactly bool True; absent or any other value = False
            no_raw = (pid in guard.FREE_ONLY or "publish_raw_results" in (fg or {})) and (fg or {}).get("publish_raw_results") is not True
            status = {"status": "ran", "calls": 0, "data_calls": 0}; pending = {}; billed = 0; short = []; recs = []
            budget = (cfg.get("run_budget_s") or {}).get(pid); t_start = clock()
            mb = (cfg.get("meter_budget") or {}).get(pid); mb = mb if mb and mb.get("pool") == pool else None; m0 = None
            for set_name, s in sets.items():
                if set_kind(set_name) != kind: continue
                out_f = Path(runs_dir) / run_date / pid / f"{set_name}.jsonl"; fh = pending.setdefault(out_f, []); reached = 0
                for item in s["rows"]:
                    if only_ids is not None and not (only_ids(pid, item["id"]) if callable(only_ids) else item["id"] in only_ids): continue
                    line = {"ts": (now_fn() if now_fn else dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds"), "provider": pid, "set": set_name,
                            "set_sha": s["sha"], "set_rows": len(s["rows"]), "set_flags": s.get("flags", []), "item_id": item["id"], "request": _request(kind, item), "offset_h": offset_h, "mock": mock}
                    if item.get("class") == "anti_bot" or item.get("category") == "anti_bot":
                        t = tos.get(pid) or {}
                        if not anti_bot_allowed(t):
                            ab = (t.get("anti_bot_scraping") or {})
                            reached += 1
                            fh.append(json.dumps(line | {"status": "skipped_tos", "pass": None, "billed_units": 0, "response_hash": None, "latency_ms": None,
                                                        "reason": f"tos_matrix: allowed={t.get('allowed')} anti_bot_scraping={ab.get('status')} checked_at={t.get('checked_at')} terms={t.get('terms_url')}"}) + "\n")
                            continue
                    est = estimate_units(pr, kind, item, pid, cfg)
                    wc = ((cfg.get("worst_case_units_per_call") or {}).get(pid) or {}).get(kind)
                    if wc is not None: est = max(est, wc)   # pre-call worst-case guard (unverified per-call cost)
                    if fg: req = guard.request_opts(pid, kind, item); est = guard.credits(pid, kind, req, None)   # F4 credit rule, requested result count
                    if mb:
                        mv = read_meter(meters, pid)
                        if mv is None:
                            status = stale("budget", status, "usage meter unreadable; failing closed (rule 6)"); break
                        lv = ledger.used(pool) * mb["ledger_unit_usd"]; m0 = m0 or (mv, lv); g = max(mv, lv)
                        if g >= mb["stop_usd"] or g + est * mb["ledger_unit_usd"] > mb["stop_usd"]:
                            status = stale("budget", status, f"max(meter ${mv:.4f}, ledger ${lv:.4f}) + next call would reach the ${mb['stop_usd']:.2f} stop (cap ${mb['cap_usd']:.2f})"); break
                    sb = (cfg.get("spend_budget") or {}).get(pid); sf = None
                    if sb:
                        try: sf = SpendFile(spend_dir, pid, spend_month(line["ts"]), sb["usd_per_1k"])
                        except SpendError as e:
                            status = stale("budget", status, str(e)); break
                        if budget_blocked(sf, est, sb["stop_usd"]):
                            sf.release()
                            status = stale("budget", status, f"MTD estimate ${sf.mtd():.4f} + next request ${est * sb['usd_per_1k'] / 1000:.4f} would exceed the ${sb['stop_usd']:.2f} stop (hard cap ${sb['cap_usd']:.2f}/month)")
                            break
                    if ledger.used(pool) + est > quota:
                        status = stale("quota", status, f"next call ~{est} units would exceed free quota {quota} (used {ledger.used(pool)})")
                        break
                    if budget is not None and clock() - t_start > budget:
                        status = stale("timeout", status, f"run budget {budget}s used up before {item['id']} ({set_name})")
                        break
                    tries = 0; halt = False
                    while True:   # one pass per provider call; contextdev gets a second pass only for the HTTP 429 retry (AC17a)
                        tries += 1; cg = None
                        if fg:   # credit guard before every attempt; the counter file is keyed by the grant-anchor window of this call's ts
                            try: ws = guard.hour_window(dt.datetime.fromisoformat(line["ts"])) if fg.get("window") == "utc_hour" else guard.anchor_window_start(dt.datetime.fromisoformat(line["ts"]), fg.get("grant_anchor_day"))
                            except guard.AnchorNotConfigured: ws = None; gmeta["guard_note"] = "grant anchor not configured; the counter never resets (fail closed)"
                            try: cg = guard.CreditGuard(gdir, pid, ws, fg["stop_at_credits"], fg.get("lock_timeout_s", 30.0))
                            except guard.GuardUnreadable:
                                status = stale("budget", status, guard.UNREADABLE); halt = True; break
                            mv = None
                            if meters and pid in meters:
                                mv = read_meter(meters, pid)
                                if mv is None:
                                    cg.release(); status = stale("budget", status, "usage meter unreadable; failing closed (rule 6)"); halt = True; break
                            eff = max(mv or 0, cg.used()); last_read = (mv, cg.used()); gmeta |= {"credits_used": eff, "credits_stop_at": fg["stop_at_credits"]}
                            if not cg.allows(est, mv):   # guard.CreditGuard.allows is the single stop check
                                cg.release()
                                status = stale("quota", status, f"credit guard: max(meter {mv}, attempts counter {last_read[1]}) = {eff} + next call {est} would pass the stop at {fg['stop_at_credits']} credits (quota {fg['quota_credits']})"); halt = True; break
                        if lim is not None and not lim.acquire():   # AC19: stop before the hourly cap, no call made
                            if cg: cg.release()
                            status = {"status": "rate_limited", "calls": status["calls"], "data_calls": status["data_calls"], "reason": f"client-side limit of {fg['rate_limit']['max_per_hour']} calls per rolling hour reached before {item['id']}; not published"}; halt = True; break
                        t0 = clock()
                        net = False
                        try: out = guard.guarded_call(sem, fn, key, item) if sem else fn(key, item)
                        except Exception as e: out = {"error": f"{type(e).__name__}: {_http_code(e)}{e}"}; net = is_network_error(e)
                        if fg and key and "error" in out: out["error"] = out["error"].replace(key, "***")   # adapters scrub too; belt and braces
                        lat = (clock() - t0) * 1000
                        qstop = retry = False
                        if fg:   # billing by status line (F5, cost math); the counter takes the attempt at its estimated credits either way
                            m = re.match(r"\S+: HTTP (\d{3})\b", str(out.get("error", "")))
                            hs = None if net else (int(m.group(1)) if m else out.get("http_status"))
                            body = guard.error_body(out.get("error")); qstop = guard.is_quota_stop(hs, body)
                            n_ret = len(out.get("results") or []) if kind == "search" and "error" not in out else None
                            units = guard.billed(pid, hs, body, guard.credits(pid, kind, req, n_ret))
                            try: cg.record(est, units)
                            except guard.GuardUnreadable: cg_fail = True
                            else: cg_fail = False
                            finally: cg.release()
                            ra = re.search(r"Retry-After: ([^;\s]+)", str(out.get("error", "")))
                            retry = tries == 1 and hs == 429 and not net and ra is not None
                            pstop = guard.paid_stop(pid, hs, out)   # keenable R1/AC20
                            rstop = pid in guard.FREE_ONLY and hs == 429 and not retry   # keenable AC19: no retry storm   # F5: only a 429 that carries Retry-After is retried, once
                        else:
                            cg_fail = False; pstop = rstop = None
                        auth = None if qstop else re.match(r"\S+: HTTP 40([13])\b", str(out.get("error", "")))   # bad or unauthorised key
                        if not fg:
                            units = 0 if net or (auth and auth.group(1) == "1") else out.get("billed_units", est)
                            if kind == "browser" and "billed_units" in out: units = browser_units(out["billed_units"], pid, pr, cfg)
                        if sf:
                            try:
                                if units: sf.add(units)
                            finally: sf.release()
                        ledger.add(pool, units); billed += units; status["calls"] += 1; status["data_calls"] += "error" not in out
                        passed, reason = (False, out["error"]) if "error" in out else _grade(kind, set_name, item, out, cfg)
                        body_s = json.dumps({k: v for k, v in out.items() if k != "file_bytes"}, sort_keys=True, default=str)
                        rec = line | {"status": "pass" if passed else "fail", "pass": passed, "reason": reason, "latency_ms": round(lat, 3),
                                      "billed_units": units, "proxy_gb": out.get("proxy_gb", 0), "http_status": out.get("http_status"),
                                      "response_hash": hashlib.sha256(body_s.encode()).hexdigest()}
                        if "config" in out: rec["config"] = out["config"]
                        if kind == "search" and no_raw: rec["results_count"] = len(out.get("results") or [])   # AC23: no raw URLs/snippets in run logs
                        elif kind == "search": rec["results"] = [{k: r.get(k) for k in ("url", "title", "snippet", "published_at")} for r in out.get("results", [])[:10]]
                        if auth: rec |= {"status": "auth_error", "pass": None}   # recorded, never scored or counted as attempted
                        elif qstop: rec |= {"status": "quota_stop", "pass": None}   # 402/401 USAGE_EXCEEDED: logged, not scored
                        elif net: rec |= {"status": "unbilled_no_response", "pass": None}
                        if pstop: rec |= {"status": "paid_usage_stop", "pass": None}
                        elif rstop: rec |= {"status": "rate_limited", "pass": None}
                        if no_raw and rec.get("reason"): rec["reason"] = "redacted (AC23)" if "error" not in out else re.sub(r"https?://\S+", "<url>", str(rec["reason"]))[:120]   # no response: 0 units, not scored, not reached
                        fh.append(json.dumps(rec) + "\n"); recs.append(rec); reached += rec["status"] in REACHED
                        if auth:   # stop this provider now; not published, its spend is kept
                            status = stale("auth_error", status, f"HTTP 40{auth.group(1)} on {item['id']} ({set_name})")
                            halt = True; break
                        if qstop:   # F5: quota stop, never retried, never an auth_error
                            status = stale("quota", status, f"HTTP {hs} USAGE_EXCEEDED on {item['id']} ({set_name}); out of credits, not retried")
                            halt = True; break
                        if pstop:   # R1/AC20: stop every further call; goes to Fred through CEO
                            status = {"status": "stopped_paid_usage", "calls": status["calls"], "data_calls": status["data_calls"], "reason": f"{pstop} on {item['id']}; no further calls; not published"}; halt = True; break
                        if rstop:
                            status = {"status": "rate_limited", "calls": status["calls"], "data_calls": status["data_calls"], "reason": f"HTTP 429 on {item['id']} ({'after the one retry' if tries > 1 else 'no Retry-After'}); stopped, not retried further; not published"}; halt = True; break
                        if cg_fail:   # the call was made but could not be counted: stop now, fail closed
                            status = stale("budget", status, guard.UNREADABLE); halt = True; break
                        if ledger.used(pool) > quota:
                            over = ledger.used(pool) - quota
                            ledger.d["over_free_units"] = ledger.d.get("over_free_units", 0) + over  # any unit past the free tier is billable
                            ledger.save(); raise CapViolation(f"{pid}: billed units exceeded free quota by {over}; $0 cap violated")
                        if retry:   # F5: 429 carries Retry-After; wait it out and retry once (the retry is its own guard entry and log line)
                            sleep(min(int(ra.group(1)) if ra.group(1).isdigit() else fg.get("retry_after_429_s", 1), 30)); continue
                        break
                    if halt: break
                if status["status"] != "ran": break
                if only_ids is None and reached < len(s["rows"]): short.append(f"{set_name} {reached}/{len(s['rows'])}")
            if status["status"] == "ran" and status["calls"] and not status["data_calls"]:
                status = stale("error", status, "every call errored")
            elif status["status"] == "ran" and short:
                status = stale("error", status, "incomplete set: " + ", ".join(short))
            elif status["status"] == "ran" and quota_degraded(recs):
                n429 = sum(map(is_rate_limited, recs))
                status = stale("quota", status, f"quota-degraded: {n429}/{status['calls']} calls answered HTTP 429 (more than 20%)")
            if not status["data_calls"] and not billed and not (fg and status["calls"]):   # nothing returned and nothing billed: no run lines, no ledger entry (a guarded provider's attempts are always logged)
                if not ledger.used(pool): ledger.d["used"].pop(pool, None)
                pending = {}
            for out_f, lines in pending.items():
                if lines:
                    out_f.parent.mkdir(parents=True, exist_ok=True)
                    if status["status"] != "ran":   # stamp the stale run's lines so tables.collect never publishes them
                        lines = [json.dumps(json.loads(l) | {"run_status": status["status"]}) + "\n" for l in lines]
                    with open(out_f, "a") as fh: fh.write("".join(lines))
            gap = meter_gap(meters, pid, m0, ledger.used(pool) * mb["ledger_unit_usd"], spend_dir, run_date) if mb and m0 else {}
            if fg and last_read and last_read[0] is not None: gap = gap | guard.meter_gap(gdir, pid, run_date, *last_read)   # logged meter vs counter gap
            summary["providers"].append(base | status | {"quota_used": ledger.used(pool), "quota": quota} | gmeta | gap)
    if not any(p.get("data_calls") for p in summary["providers"]) and json.dumps(ledger.d, sort_keys=True) == used_start:
        return summary   # nothing returned data and nothing billed: leave ledger and runs untouched
    ledger.save()
    if ledger.d.get("over_free_units", 0) > 0: raise CapViolation("ledger shows usage past the free tier (billed $ > 0)")
    sp = Path(runs_dir) / run_date / f"_run-offset{offset_h}.json"; sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(summary, indent=1)); return summary
