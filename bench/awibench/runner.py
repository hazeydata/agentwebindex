"""Benchmark runner: free-tier only, hard $0 cap (D4), quota halting (AC11), ToS skips (D2/AC12),
run log per §4: runs/YYYY-MM-DD/<provider>/<set>.jsonl.

Provider-level outcomes: skipped_no_key, skipped_no_vendor_confirmation (contextdev/keenable live gate, R9/R22), not_implemented, skipped_no_free_tier, ran, and stale_<reason>.
Keenable (AWI-PROVIDER-KEENABLE-001): stopped_paid_usage (R1/AC20: HTTP 402, a usage block, billed units > 0, or paid_usage_allowed not false;
no further call) and rate_limited (AC19: a 429 without Retry-After, or a second 429 after the one retry, or the client-side limiter's hourly
cap). Both are, like stale_<reason>, never published; their run-log lines are stamped run_status. A keyless provider (guard.KEYLESS)
needs no env key. Keenable run-log lines carry no raw result URLs or snippets (AC23): only a count and the response hash.
CEO ruling 2026-10-19: a row publishes ONLY when the run covered the full set (items reached == set size). Any incomplete
run is stale_<reason>, reason one of STALE_REASONS:
  quota      - the free-quota guard stopped it before the next call, or (config stop_on_http) N consecutive HTTP 429 answers
  timeout    - the provider's run budget (config run_budget_s) ran out
  auth_error - any HTTP 401/403 stops the provider at the first one
  budget     - the paid spend guard (config spend_budget) stopped it before the next request, or (config stop_on_http) its first HTTP 402
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
import calendar, datetime as dt, fcntl, hashlib, json, math, os, re, time
from decimal import Decimal
from pathlib import Path
from .util import PUBLISHED_DIRS, is_under, not_captured
from . import checks, cost, guard, gates

SET_KIND = {"search_evergreen": "search", "scrape_urls": "scrape", "browser_tasks": "browser"}  # news_* -> search

def set_kind(name):
    if name.startswith("newsfetch_"): return "browser"   # browser fetch of the frozen news set's article URLs (awibench.newsfetch)
    return "search" if name.startswith("news_") else SET_KIND[name]

STALE_REASONS = ("quota", "timeout", "auth_error", "error", "budget", "paid_usage", "metered_budget", "rate_limited")
# spec AWI-RUN-2026-10-26-APIFY-001 names (AC2, AC5, AC6; R4): the metered (Apify) spend guard is stopped_budget, a 429 storm is
# stopped_rate_limited. Every other reason keeps stale_<reason> (e.g. the Bright Data spend_budget guard stays stale_budget).
STOP_STATUS = {"paid_usage": "stopped_paid_usage", "metered_budget": "stopped_budget", "rate_limited": "stopped_rate_limited"}
STOPPED_PREFIXES = ("stale_", "stopped_")   # an incomplete run: never published
PROVIDER_STOPS = ("stale_auth_error", "stopped_paid_usage")   # stop every category of the provider for the rest of the run
# Apify error codes that mean out of credit / plan limit even when the HTTP status is 200 (AC5)
PAID_BODY = re.compile(r"\b(payment-required|usage-limit-exceeded|platform-feature-disabled|not-enough-usage-to-run-paid-actor)\b")
RETRY_AFTER = re.compile(r"Retry-After: ?(\d+(?:\.\d+)?)")

def is_timeout_error(e):
    """A client-side timeout (socket.timeout / TimeoutError, or urllib's URLError wrapping one). AC4: counted as a fail, still logged."""
    import socket, urllib.error
    return isinstance(e, (TimeoutError, socket.timeout)) or (isinstance(e, urllib.error.URLError) and isinstance(e.reason, (TimeoutError, socket.timeout)))

def _atomic_write(path, text):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True); t = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    t.write_text(text); os.replace(t, path)

class UnknownCost(ValueError): pass
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
    return {"status": STOP_STATUS.get(reason, f"stale_{reason}"), "calls": status["calls"], "data_calls": status["data_calls"], "reason": f"{why}; not published"} | ({"cap_overrun": status["cap_overrun"]} if status.get("cap_overrun") else {})

class CapViolation(RuntimeError): pass

class LedgerError(RuntimeError): pass

class QuotaLedger:
    def __init__(self, path, month):
        self.path = Path(path); self.d = json.loads(self.path.read_text()) if self.path.exists() else None
        if self.path.exists() and not isinstance(self.d, dict):   # null, [], "x": unreadable counter, fail closed (AC2), never overwrite
            raise LedgerError(f"quota ledger {self.path.name} is not a JSON object ({type(self.d).__name__}); failing closed, no call made")
        if self.d is None or self.d.get("month") != month:   # calendar-month ledger: a new month starts at zero on the 1st
            self.d = {"month": month, "used": {}, "over_free_units": 0}
    def used(self, pool): return self.d["used"].get(pool, 0)
    def add(self, pool, units): self.d["used"][pool] = self.used(pool) + units
    def save(self): self.path.parent.mkdir(parents=True, exist_ok=True); t = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp"); t.write_text(json.dumps(self.d, indent=1, sort_keys=True)); os.replace(t, self.path)   # atomic

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
    if ft.get("metered") is False and pid in guard.KEYLESS:   # AC24: keyless, unmetered public pool (no monthly figure); quota = units_per_hour
        q = ft.get("units_per_hour")
        if not_captured(q) or not isinstance(q, int) or q <= 0: return False, "keyless free tier units_per_hour NOT CAPTURED or zero"
        return (True, "free tier (keyless, unmetered)") if ft.get("needs_card") is False else (False, "free tier needs a card (or unknown)")
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
    log["runs"].append({"run_date": run_date} | d); _atomic_write(f, json.dumps(log, indent=1, sort_keys=True))
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
    if m == "credits": return 1   # a credits row without credits_per_call: 1 credit; metered USD pools refuse this in run() (AC2)
    if m == "per_hour_browser":
        t = (pricing.get("tiers") or {}).get("entry") or {}
        mm = 0 if not_captured(t.get("min_minutes")) else t["min_minutes"]; rr = 0 if not_captured(t.get("rounding_minutes")) else t["rounding_minutes"]
        return cost.billed_minutes(item.get("timeout_s", 60) / 60, mm, rr)
    if m == "per_gb": return (pricing.get("est_gb_per_call") or {}).get(kind, 0.01)
    if m in ("per_request", "tier"): return 1   # one request = one unit
    raise UnknownCost(f"unknown pricing model {m!r}")

def _request(kind, item):
    return {"search": lambda: {"query": item["query"]}, "scrape": lambda: {"url": item["url"]},
            "browser": lambda: {"task": item["id"], "start_url": item["start_url"]}}[kind]()

def _grade(kind, set_name, item, out, cfg):
    if kind == "search":
        return checks.news_check(item, out.get("results", [])) if set_name.startswith("news_") else checks.search_check(item["check"], out.get("results", []))
    if kind == "scrape": return checks.scrape_check(item, out, cfg["challenge_signatures"], cfg.get("challenge_html_signatures", ()))
    return checks.browser_check(item, out)

def live_gated(pid, t_e, fg):
    """R9/R26 (C07): guard.LIVE_GATED or guard.KEYLESS ids, any tos entry that requires vendor confirmation, or any guard block with a
    live_run_allowed key: such a provider is called only if guard.live_allowed() is True."""
    return pid in guard.LIVE_GATED or pid in guard.KEYLESS or t_e.get(gates.REQUIRED_FLAG) is True or bool(fg and "live_run_allowed" in fg)

def gate(p, kind, pr, tos, cfg, env, adapters):
    """The skip checks for one (provider, category), in run order. None = it will be called; else the summary status/reason.
    run() and check_run() both use it, so the R41 projection plans exactly the providers the run calls."""
    pid = p["id"]; t_e = tos.get(pid) or {}
    if not gates.vendor_confirmed(t_e):   # R10: gated and unconfirmed -> never called, whatever the key/config say
        return {"status": "skipped_no_vendor_confirmation", "reason": "tos_matrix vendor confirmation required and not recorded; no call made"}
    fc = forbidden_clause(t_e)
    if fc: return {"status": "excluded_tos_benchmarking", "reason": f"terms forbid {fc.replace('_', ' ')}: {t_e[fc].get('quote')} ({t_e.get('terms_url')}, checked {t_e.get('checked_at')})"}
    m1 = cfg.get("month1_free_tier_providers")
    if m1 is not None and pid not in m1:
        return {"status": "skipped_not_month1", "reason": "not on the month-one free-quota list (config month1_free_tier_providers)"}
    fg = (cfg.get("free_tier_guard") or {}).get(pid)
    if fg and pid in guard.FREE_ONLY and fg.get("paid_usage_allowed") is not False:   # Keenable R1: missing or non-bool fails closed
        return {"status": "stopped_paid_usage", "reason": f"config free_tier_guard.{pid}.paid_usage_allowed must be bool false; no call made"}
    # Keenable R5: the keyless placeholder never bypasses the live gate. A missing key, null record or missing block fails closed.
    if live_gated(pid, t_e, fg) and not guard.live_allowed(cfg, tos, pid):
        return {"status": "skipped_no_vendor_confirmation", "reason": f"live_run_allowed is not true or vendor_confirmation is not recorded (config free_tier_guard.{pid}); no call made"}
    if pid in guard.LIVE_GATED and not fg: raise CapViolation(f"{pid}: free_tier_guard.{pid} missing; the credit guard must always apply")   # unreachable: live_allowed needs the block
    key = "keyless" if pid in guard.KEYLESS else env.get(p["key_env"])   # keenable: keyless public endpoint (AC18)
    if not key: return {"status": "skipped_no_key", "reason": "not run (no key)"}
    if (adapters.get(pid) or {}).get(kind) is None: return {"status": "not_implemented"}
    ok, why = eligibility(pid, pr, cfg)
    if not ok: return {"status": "skipped_no_free_tier", "reason": why}
    return None

def call_estimate(pid, kind, item, pr, cfg, fg, mb, pool):
    """(estimated units of one call, guarded request opts or None). Raises UnknownCost when the per-call cost is unknown and config has
    no worst_case_units_per_call for it. The pre-call guards and the R41 projection use this same number."""
    wc = ((cfg.get("worst_case_units_per_call") or {}).get(pid) or {}).get(kind); req = None
    try:
        if mb and pr.get("model") in ("credits", "per_request", "tier") and kind not in (pr.get("credits_per_call") or {}):   # USD-metered pool: 1 is not a cost
            raise UnknownCost(f"{pr.get('model')} model with no credits_per_call.{kind} on metered pool {pool}")
        est = estimate_units(pr, kind, item, pid, cfg)
    except UnknownCost:
        if wc is None: raise
        est = wc
    if wc is not None: est = max(est, wc)   # pre-call worst-case guard (unverified per-call cost)
    if fg: req = guard.request_opts(pid, kind, item); est = guard.credits(pid, kind, req, None)   # F4 credit rule, requested result count
    return est, req

class RunRefused(RuntimeError):
    """R41 (as narrowed by R43/R44): the whole run is refused before any provider call, exit 2 in the collector, for a missing price,
    a missing cap, an unknown provider, a live provider with no cap entry, an unknown per-call cost or an unreadable counter/spend file.
    A projected cap breach is NOT a refusal any more (spec R44.3): only that provider is dropped (skipped_cap). .report is the
    over_cap_projected.json body."""
    def __init__(self, report): super().__init__(report["reason"]); self.report = report

OVER_CAP_FILE = "over_cap_projected.json"
SKIPPED_CAP = "skipped_cap"   # spec R43/R44: a provider projected over its remaining month budget is dropped with 0 calls (run continues, exit 0)
SKIPPED_RATE, SKIPPED_FLOOR, SKIPPED_BALANCE_UNKNOWN, SKIPPED_RESERVE = "skipped_rate", "skipped_floor", "skipped_balance_unknown", "skipped_reserve"   # spec R45
SKIP_STATUSES = (SKIPPED_CAP, SKIPPED_RATE, SKIPPED_FLOOR, SKIPPED_BALANCE_UNKNOWN, SKIPPED_RESERVE)   # R44.3/R45: one provider dropped with 0 calls, the rest run, exit 0
SKIPPED_CAP_FILE = "_skipped_cap.jsonl"   # bench/runs/<run_date>/: one row per dropped provider per run (R43.2 fields), append-only

def full_reserve(cfg, now, full):
    """Owner call (Lucille 2, AWI owner, 2026-10-27 5:33 AM ET; recorded earlier as 5:34), call 3: the quota-limited providers are always kept for the weekly
    full runs, and every other run (daily smoke, re-probes) drops them first. Returns {pid: units} that a non-full run must leave
    unspent: run_schedule.full_reserve_units[pid] (the worst-case units of one trimmed full run) x the weekly full runs still to come
    this calendar month (run_schedule.full_cron_utc, the benchmark.yml full cron, strictly after now). {} for a full run."""
    sched = cfg.get("run_schedule") or {}; per = sched.get("full_reserve_units") or {}
    if full or not per: return {}
    c = sched["full_cron_utc"]; now = now.astimezone(dt.timezone.utc)
    n = sum(1 for d in range(1, calendar.monthrange(now.year, now.month)[1] + 1)
            if dt.date(now.year, now.month, d).weekday() == c["weekday"] and dt.datetime(now.year, now.month, d, c["hour"], c["minute"], tzinfo=dt.timezone.utc) > now)
    return {pid: u * n for pid, u in per.items()} if n else {}

def worst_case_units(sq, kind, est):
    """Owner call (Lucille 2, 2026-10-27 6:31 AM ET): AWI never makes a shared-quota call whose WORST-CASE units could take the shared
    balance below floor_credits (10,000, above Firecrawl's documented default auto-recharge threshold of 1,000; the account's real
    threshold is unverified, config shared_quota.firecrawl.auto_recharge_note). Worst case per call = max(the priced estimate,
    shared_quota.<pid>.worst_case_units_per_call[kind]); a kind with no worst-case entry returns None (the caller fails closed)."""
    w = _dec(((sq or {}).get("worst_case_units_per_call") or {}).get(kind)); e = _dec(est)
    return None if w is None or w < 0 or e is None else max(w, e)

def floor_line(pid, f):
    """Owner call (Lucille 2, 2026-10-27 6:20 AM ET): the job-log line that tells the owner a shared-quota balance hit its floor."""
    return f"{pid}_floor_reached: shared balance {f['balance_credits']} credits, floor {f['floor_credits']} credits, next {f['planned_units']} units; {pid} skipped ({f['where']})"

def limit_sets(sets, lim):
    """Spec R44.2 run-size limits for one provider: lim = {kind: max items per set of that kind} (0 = none). A trimmed set keeps
    its first n rows and records sample_of (the full size), so set_rows is the sample actually run and the scorecard notes it."""
    if not lim: return sets
    out = {}
    for name, s in sets.items():
        n = lim.get(set_kind(name))
        if n is None or n >= len(s["rows"]): out[name] = s
        elif n > 0: out[name] = s | {"rows": s["rows"][:n], "sample_of": len(s["rows"])}
    return out

def _dec(v):
    """Exact Decimal of a config/ledger number via its shortest repr (0.1 -> Decimal('0.1')), so no float edge errors; None if not a number."""
    return Decimal(repr(v)) if type(v) in (int, float) and math.isfinite(v) else None

def _posint(v): return type(v) is int and v > 0

def _s(d): return None if d is None else format(d.normalize(), "f")   # exact, no trailing zeros ("0.15")

def list_price_per_unit(pr):
    """List price of one billing unit: the cost.cost_columns() entry-tier price (per 1k, at list price) for a 1-unit line, read
    exactly (float noise below $0.000001 per 1k dropped). None when cost_columns has no price (NOT CAPTURED, no tiers)."""
    d = _dec(cost.cost_columns(pr, [{"status": "pass", "billed_units": 1}])["entry_tier_cost_per_1k"])
    return None if d is None else d.quantize(Decimal("0.000001")) / 1000

def _counter_used(gdir, pid, fg, now):
    """Attempts already on the credit-guard counter for the window of `now` (read-only, no lock). 0 when absent; None if unreadable."""
    if fg.get("window") == "utc_hour": w = guard.hour_window(now)
    else:
        try: w = guard.anchor_window_start(now, fg.get("grant_anchor_day")).isoformat()
        except guard.AnchorNotConfigured: w = guard.UNANCHORED
    f = Path(gdir) / f"{pid}-{w}.json"
    if not f.exists(): return 0
    try: d = json.loads(f.read_text())
    except (OSError, ValueError): return None
    return d["attempted"] if guard._valid(d, pid, w) else None

def _spend_mtd(spend_dir, pid, month):
    """Month-to-date USD from the provider's spend file (read-only). 0 when absent; None if unreadable or invalid."""
    f = Path(spend_dir) / f"{pid}-{month}.json"
    if not f.exists(): return Decimal(0)
    try: d = json.loads(f.read_text())
    except (OSError, ValueError): return None
    return _dec(d["est_usd"]) if valid_spend(d, pid, month) else None

def provider_problems(providers, cfg, tos, mock=False):
    """R41.2: the run stops before any call for a provider absent from config known_providers, or a provider set to live (it passes
    guard.live_allowed) with no cap entry (free_tier_guard quota_credits and stop_at_credits; keenable also rate_limit.max_per_hour).
    Known providers that are off for month one are not problems (skipped_not_month1). A missing or null known_providers is accepted
    only in mock runs (mock provider ids are not real); a live run without the list fails closed."""
    known = cfg.get("known_providers")
    if known is None and mock: pass
    elif not isinstance(known, list) or not known or not all(type(k) is str for k in known):
        return ["config known_providers missing or not a list of provider ids; failing closed"]
    out = []
    for p in providers:
        pid = p["id"]; fg = (cfg.get("free_tier_guard") or {}).get(pid)
        if known is not None and pid not in known: out.append(f"{pid}: unknown provider (not in config known_providers)"); continue
        if not live_gated(pid, tos.get(pid) or {}, fg) or not guard.live_allowed(cfg, tos, pid): continue
        miss = [k for k in ("quota_credits", "stop_at_credits") if not _posint(fg.get(k))]
        if (pid in guard.FREE_ONLY or fg.get("window") == "utc_hour") and not _posint((fg.get("rate_limit") or {}).get("max_per_hour")): miss.append("rate_limit.max_per_hour")
        if miss: out.append(f"{pid}: set to live with no cap entry (config free_tier_guard.{pid}.{', '.join(miss)} missing or not a positive int)")
    return out

def check_run(*, providers, sets, pricing, tos, adapters, cfg, env, ledger, spend_dir, guard_dir, run_date, now, mock=False, only_ids=None, limits=None, meters=None, reserve=None):
    """R41, once per run before any provider call. Returns the over_cap_projected.json report; report["refused"] True = 0 calls.
    1. provider_problems (R41.2).
    2. For every provider that will make calls (gate() is None), per billing pool: planned_calls = the set items the run would call
       (only_ids applied, ToS-skipped anti-bot items excluded, no retries), planned_units = their call_estimate() units, price = the
       cost.cost_columns() list price, projected_usd = planned_units x unit price (exact Decimal).
       Cap: meter_budget (stop_usd minus ledger pool x ledger_unit_usd) or spend_budget (stop_usd minus the spend file's month-to-date
       est_usd): refuse if projected_usd > remaining_usd. Otherwise the free quota (D4: the $0 cap is spent only from free units):
       remaining units = quota minus the ledger, and for a free_tier_guard provider also stop_at_credits minus its counter; refuse if
       planned_units > remaining units (remaining_usd = those units at list price). Exactly at the cap runs.
       Keenable (FREE_ONLY / utc_hour window) also: planned_calls + this UTC hour's counter must be <= rate_limit.max_per_hour (899).
    3. A missing price, cap, unknown per-call cost or unreadable counter/spend file refuses the run (fail closed): report["refused"].
    4. Spec R44.3 (supersedes R43's whole-run skip and R41's whole-run refusal for cap breaches): a provider whose projection is over its
       remaining cap is listed in report["skipped_cap"] (every dropped provider, each with its status) and is NOT a refusal; run() drops
       only that provider (0 calls to it), the rest run, exit 0. Status (spec R45): skipped_cap (USD stop or free quota), skipped_rate
       (keenable's 899/hour limit, R45 b: planned_per_hour, limit), skipped_floor (Firecrawl shared balance below floor_credits, R45.3),
       skipped_balance_unknown (shared meter unreadable, R45.3), skipped_reserve (eats into the full-run reserve, R45.4). limits = {pid: {kind: n}} (R44.2) trims that provider's sets before planning, exactly as run() does.
    5. Owner call 3 (2026-10-27 5:33 AM ET): reserve = {pid: units} (full_reserve) that this non-full run must leave unspent for the
       weekly full runs still to come; a provider whose planned units would eat into it is dropped (skipped_reserve). A cfg shared_quota
       provider (firecrawl: its key is shared with Fred's other tools) also caps remaining units at the shared meter's remaining
       credits minus floor_credits (skipped_floor); an unreadable shared meter drops it (skipped_balance_unknown, fail closed)."""
    rep = refusal_report(run_date, now, provider_problems(providers, cfg, tos, mock))
    if not rep["problems"]:
        plan = {}
        for p in providers:
            pid = p["id"]; fg = (cfg.get("free_tier_guard") or {}).get(pid)
            for kind in p["categories"]:
                pr = (pricing.get(pid) or {}).get(kind) or {}
                if gate(p, kind, pr, tos, cfg, env, adapters) is not None: continue
                pool = pr["free_tier"].get("pool") or pid
                mb = (cfg.get("meter_budget") or {}).get(pid); mb = mb if mb and mb.get("pool") == pool else None
                row = plan.setdefault((pid, pool), {"pid": pid, "pool": pool, "fg": fg, "mb": mb, "kinds": {}, "unknown": None})
                calls = []
                for set_name, s in limit_sets(sets, (limits or {}).get(pid)).items():
                    if set_kind(set_name) != kind: continue
                    for item in s["rows"]:
                        if only_ids is not None and not (only_ids(pid, item["id"]) if callable(only_ids) else item["id"] in only_ids): continue
                        if (item.get("class") == "anti_bot" or item.get("category") == "anti_bot") and not anti_bot_allowed(tos.get(pid) or {}): continue
                        try: calls.append(call_estimate(pid, kind, item, pr, cfg, fg, mb, pool)[0])
                        except UnknownCost as e: row["unknown"] = f"unknown per-call cost ({e})"
                q = pr["free_tier"]["units_per_month"] if pr["free_tier"].get("units_per_month") is not None else pr["free_tier"].get("units_per_hour")
                if calls or row["unknown"]: row["kinds"][kind] = {"calls": calls, "price": list_price_per_unit(pr), "quota": q}
        for (pid, pool), r in plan.items():
            if not r["kinds"]: continue
            rep["providers"].append(_project(r, cfg, ledger, spend_dir, guard_dir, now, meters, (reserve or {}).get(pid)))
        bad = [x for x in rep["providers"] if x["reason"] and not x.get("cap_breach")]
        rep["problems"] += [f"{x['provider']}: {x['reason']}" for x in bad]
    skipped = [x for x in rep["providers"] if x.get("cap_breach")]
    return refusal_report(run_date, now, rep["problems"], rep["providers"], skipped)

def refusal_report(run_date, now, problems, providers=(), skipped=()):
    """The over_cap_projected.json body: refused (True when any problem: the whole run stops, exit 2), reason, problems, skipped_cap
    (spec R44.3: providers projected over their remaining cap, dropped with 0 calls while the rest run), and per-provider projection
    rows (planned_calls, price, projected_usd, remaining_usd, reason)."""
    sk = [{"provider": x["provider"], "status": x.get("skip_status", SKIPPED_CAP), "categories": x["categories"], "projected_usd": x["projected_usd"], "remaining_usd": x["remaining_usd"], "reason": x["reason"]}
          | {k: x[k] for k in ("planned_per_hour", "limit", "reserve_units") if k in x} for x in skipped]
    reason = ("run refused before any provider call (R41; returns to Side Hustle CEO): " + "; ".join(problems)) if problems else \
             ("providers skipped on cap before any call (R44.3; the rest of the run continues): " + "; ".join(f"{x['provider']}: {x['reason']}" for x in sk)) if sk else None
    return {"artifact": OVER_CAP_FILE, "spec": "awi-provider-contextdev-001 R41, R43, R44, R45", "run_date": run_date, "checked_at": now.isoformat(timespec="seconds"),
            "refused": bool(problems), "reason": reason, "problems": list(problems), "skipped_cap": sk, "providers": list(providers)}

def _project(r, cfg, ledger, spend_dir, gdir, now, meters=None, reserve=None):
    pid, pool, fg, mb = r["pid"], r["pool"], r["fg"], r["mb"]
    calls = [u for k in r["kinds"].values() for u in k["calls"]]; n = len(calls)
    units = sum((_dec(u) for u in calls), Decimal(0)) if all(_dec(u) is not None for u in calls) else None
    out = {"provider": pid, "pool": pool, "categories": sorted(r["kinds"]), "planned_calls": n, "planned_units": _s(units),
           "price": None, "price_usd_per_unit": {k: _s(v["price"]) for k, v in sorted(r["kinds"].items())},
           "projected_usd": None, "remaining_usd": None, "cap": None, "reason": None}
    def fail(why): out["reason"] = why; return out
    def breach(why, status=SKIPPED_CAP, **extra): out.update(cap_breach=True, skip_status=status, **extra); return fail(why)   # R44.3/R45: drop this provider only, not a refusal
    if r["unknown"]: return fail(r["unknown"])
    if units is None: return fail("planned units are not numbers")
    if any(v["price"] is None for v in r["kinds"].values()):
        return fail("list price missing (cost.cost_columns entry tier n/a for " + ", ".join(k for k, v in sorted(r["kinds"].items()) if v["price"] is None) + ")")
    proj = sum((_dec(u) * v["price"] for v in r["kinds"].values() for u in v["calls"]), Decimal(0))
    out["projected_usd"] = _s(proj); out["price"] = _s((proj / n).quantize(Decimal("1E-9"))) if n else "0"
    sb = (cfg.get("spend_budget") or {}).get(pid)
    if mb or sb:   # USD cap
        b = mb or sb; stop = _dec(b.get("stop_usd"))
        if stop is None: return fail(f"cap missing ({'meter_budget' if mb else 'spend_budget'}.{pid}.stop_usd)")
        if mb:
            lu = _dec(mb.get("ledger_unit_usd")); used = _dec(ledger.used(pool))
            if lu is None or used is None: return fail(f"cap missing (meter_budget.{pid}.ledger_unit_usd or ledger pool {pool} not a number)")
            mtd = used * lu
        else:
            mtd = _spend_mtd(spend_dir, pid, now.astimezone(dt.timezone.utc).strftime("%Y-%m"))
            if mtd is None: return fail(f"month-to-date spend file for {pid} unreadable or invalid; failing closed")
        usd_rem = stop - mtd; out["remaining_usd"] = _s(usd_rem); out["cap"] = {"kind": "usd", "stop_usd": _s(stop), "month_to_date_usd": _s(mtd)}
        if proj > usd_rem: return breach(f"projected ${_s(proj)} > remaining ${_s(usd_rem)} (stop ${_s(stop)} minus month-to-date ${_s(mtd)})")
    else: usd_rem = None   # the free quota below is the cap (and, for a USD-capped provider, a second cap the per-call quota guard also enforces)
    qs = {v["quota"] for v in r["kinds"].values()}
    q = qs.pop() if len(qs) == 1 else None
    if q is None or _dec(q) is None or q <= 0: return fail(f"cap missing (free-tier quota for pool {pool})")
    cap = _dec(q); used = _dec(ledger.used(pool))
    if used is None: return fail(f"ledger pool {pool} not a number; failing closed")
    rem_u = cap - used; info = {"kind": "free_quota_units", "quota_units": _s(cap), "ledger_used_units": _s(used)}
    if fg:
        cap = min(cap, _dec(fg["quota_credits"])); rem_u = cap - used
        cu = _counter_used(gdir, pid, fg, now)
        if cu is None: return fail(guard.UNREADABLE)
        rem_u = min(rem_u, _dec(fg["stop_at_credits"]) - cu); info |= {"quota_units": _s(cap), "stop_at_credits": fg["stop_at_credits"], "counter_used": cu}
    sq = (cfg.get("shared_quota") or {}).get(pid)
    if sq:   # owner call 3: a key shared with Fred's other tools; AWI never takes the shared balance below floor_credits
        floor = _dec(sq.get("floor_credits"))
        if floor is None or floor < 0: return fail(f"cap missing (shared_quota.{pid}.floor_credits)")
        thr = _dec(sq.get("auto_recharge_threshold_default"))   # Firecrawl billing code default 1000 (config auto_recharge_note)
        if (thr is None and (pid == "firecrawl" or "auto_recharge_threshold_default" in sq)) or (thr is not None and not floor > thr): return fail(f"shared_quota.{pid}.floor_credits must be greater than auto_recharge_threshold_default ({sq.get('auto_recharge_threshold_default')}); failing closed")
        mv = read_meter(meters, pid)
        if mv is None:
            out["floor_reached"] = {"reached": None, "balance_credits": None, "floor_credits": _s(floor), "where": "pre-run"}
            return breach("shared-quota meter unreadable; failing closed (R45.3: not a call)", SKIPPED_BALANCE_UNKNOWN)
        wk = {k: [worst_case_units(sq, k, u) for u in v["calls"]] for k, v in r["kinds"].items()}
        if any(w is None for ws in wk.values() for w in ws): return fail(f"cap missing (shared_quota.{pid}.worst_case_units_per_call for " + ", ".join(sorted(k for k, ws in wk.items() if any(w is None for w in ws))) + ")")
        wunits = sum((w for ws in wk.values() for w in ws), Decimal(0))   # owner call 6:31 AM ET: the floor check uses worst-case units
        mv = _dec(mv); hit = wunits > mv - floor   # owner call 2026-10-27 6:20 AM ET: skip whenever the balance is (or would go) below the floor
        out["floor_reached"] = {"reached": hit, "balance_credits": _s(mv), "floor_credits": _s(floor), "planned_units": _s(wunits), "where": "pre-run"}
        out["worst_case_units"] = _s(wunits)
        rem_u = min(rem_u, mv - floor); info |= {"shared_remaining_credits": _s(mv), "shared_floor_credits": _s(floor)}
    rv = _dec(reserve) if reserve else None
    if rv: info |= {"reserved_for_full_runs_units": _s(rv)}
    q_rem_usd = rem_u * proj / units if units else Decimal(0)
    out["cap"] = info if usd_rem is None else out["cap"] | {"free_quota": info}; out["remaining_units"] = _s(rem_u)
    out["remaining_usd"] = _s(q_rem_usd if usd_rem is None else min(usd_rem, q_rem_usd))
    if sq and out["floor_reached"]["reached"]:
        return breach(f"{pid}_floor_reached: shared balance {out['floor_reached']['balance_credits']} credits minus planned worst-case {out['worst_case_units']} units would go below the floor of {out['floor_reached']['floor_credits']} credits kept for Fred's other tools", SKIPPED_FLOOR)
    hourly = bool(fg) and (pid in guard.FREE_ONLY or fg.get("window") == "utc_hour")
    if hourly:   # spec R45 (b): keenable's 899/hour limit is a per-provider skip (skipped_rate), never a whole-run refusal
        mh = fg["rate_limit"]["max_per_hour"]; hu = _counter_used(gdir, pid, fg, now) if fg.get("window") == "utc_hour" else 0
        out["cap"] = out["cap"] | {"max_per_hour": mh, "this_hour_used": hu}
        rate = {"planned_per_hour": n + hu, "limit": mh}
    if units > rem_u:
        why = f"planned {_s(units)} units > remaining {_s(rem_u)} free units (projected ${_s(proj)} > remaining ${out['remaining_usd']})"
        if hourly and fg.get("window") == "utc_hour": return breach(why, SKIPPED_RATE, **rate)   # the utc_hour window quota is the hourly limit
        return breach(why)
    if hourly and n + hu > mh: return breach(f"{n} planned calls + {hu} this UTC hour > {mh} per hour", SKIPPED_RATE, **rate)
    if rv and units > rem_u - rv: return breach(f"planned {_s(units)} units > remaining {_s(rem_u)} minus {_s(rv)} reserved for the weekly full runs left this month (owner call 3; R45.4)", SKIPPED_RESERVE, reserve_units=_s(rv))
    return out

def run(*, providers, sets, pricing, tos, adapters, cfg, runs_dir, ledger_path, run_date, env, mock=False,
        offset_h=0, only_ids=None, clock=time.perf_counter, now_fn=None, spend_dir=None, meters=None, guard_dir=None, sleep=time.sleep, preflight=True, limits=None, reserve=None):
    """sets: {set_name: {"rows": [...], "sha": str}}. Returns summary dict. Raises CapViolation on any billed $ > 0.
    R41: unless preflight=False (tests of the per-call backstop guards only; the live collector never passes it), check_run() runs
    once before any provider call and raises RunRefused (0 calls, nothing written) on an unknown provider, a live provider with
    no cap entry, a missing price or cap, or an unreadable counter/spend file. Spec R44.3: a projected cap breach drops only that
    provider: 0 calls to it, a skip row per category (status skipped_cap / skipped_rate / skipped_floor / skipped_balance_unknown /
    skipped_reserve per spec R45; projected, remaining, run_id; skipped_rate adds planned_per_hour and limit), one line in
    bench/runs/<run_date>/_skipped_cap.jsonl, a printed `skipped: <cap|rate|floor|balance_unknown|reserve> <provider> projected $X >
    remaining $Y`, and summary["over_cap_projected"] (the collector writes alerts/over_cap_projected.json); the other providers run.
    R45.3: the shared-quota balance is checked again before every call; below the floor or unreadable stops that provider's calls
    (status stale_quota, so its partial lines stay unpublished, with skip skipped_floor / skipped_balance_unknown and a skip-file row).
    limits (R44.2) trims a provider's sets."""
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
    skipped_cap = {}; floor_hits = summary["floor_reached"] = {}
    t_run = now_fn() if now_fn else dt.datetime.now(dt.timezone.utc)
    run_id = f"{run_date}-offset{offset_h}-{t_run.astimezone(dt.timezone.utc).strftime('%H%M%S')}"
    def skip_row(row):   # R43.2/R45: one append-only line per dropped provider per run in bench/runs/<run_date>/_skipped_cap.jsonl
        sf = Path(runs_dir) / run_date / SKIPPED_CAP_FILE; sf.parent.mkdir(parents=True, exist_ok=True)
        with open(sf, "a") as fh: fh.write(json.dumps(row | {"run_id": run_id, "run_date": run_date, "offset_h": offset_h, "mock": mock}, sort_keys=True) + "\n")   # owner call 2026-10-27 6:20 AM ET: <pid>_floor_reached signal per shared_quota provider
    if preflight:   # R41.1/R41.2: once per run, before any provider call; hard problems refuse the whole run, cap breaches drop one provider (R44.3)
        rep = check_run(providers=providers, sets=sets, pricing=pricing, tos=tos, adapters=adapters, cfg=cfg, env=env, ledger=ledger, spend_dir=spend_dir,
                        guard_dir=gdir, run_date=run_date, mock=mock, only_ids=only_ids, limits=limits, meters=meters, reserve=reserve, now=t_run)
        if rep["refused"]: raise RunRefused(rep)
        for x in rep["providers"]:
            if x.get("floor_reached"):
                floor_hits[x["provider"]] = x["floor_reached"]
                if x["floor_reached"]["reached"]: print(floor_line(x["provider"], x["floor_reached"]))
        if rep["skipped_cap"]:
            summary["over_cap_projected"] = rep
            skipped_cap = {x["provider"]: x | {"run_id": run_id} for x in rep["skipped_cap"]}
            for x in skipped_cap.values():
                print(f"skipped: {x['status'].removeprefix('skipped_')} {x['provider']} projected ${x['projected_usd']} > remaining ${x['remaining_usd']} ({x['reason']})")
                skip_row({"status": x["status"], "provider": x["provider"], "projected_usd": x["projected_usd"], "remaining_usd": x["remaining_usd"], "reason": x["reason"]}
                         | {k: x[k] for k in ("planned_per_hour", "limit", "reserve_units") if k in x})
    for p in providers:
        pid = p["id"]; pp = pricing.get(pid, {}); pstop = None
        for kind in p["categories"]:
            pr = pp.get(kind) or {}
            base = {"provider": pid, "category": kind}
            sc = skipped_cap.get(pid)
            if sc and kind in sc["categories"]:   # R44.3: dropped on cap before any call; the rest of the run continues
                summary["providers"].append(base | {"status": sc["status"], "calls": 0, "data_calls": 0, "projected_usd": sc["projected_usd"],
                                                    "remaining_usd": sc["remaining_usd"], "run_id": sc["run_id"], "reason": f"{sc['status']}: {sc['reason']}"}
                                            | {k: sc[k] for k in ("planned_per_hour", "limit", "reserve_units") if k in sc}); continue
            if pstop:   # AC5: a 401/403/402 (or Apify payment/usage-limit error) stops the whole provider, every category
                summary["providers"].append(base | {"status": pstop["status"], "calls": 0, "data_calls": 0, "reason": f"provider stopped earlier this run: {pstop['reason']}"}); continue
            t_e = tos.get(pid) or {}; fg = (cfg.get("free_tier_guard") or {}).get(pid)
            skip = gate(p, kind, pr, tos, cfg, env, adapters)
            if skip: summary["providers"].append(base | skip); continue
            key = "keyless" if pid in guard.KEYLESS else env.get(p["key_env"]); fn = (adapters.get(pid) or {}).get(kind)
            pool = pr["free_tier"].get("pool") or pid; quota = pr["free_tier"]["units_per_month"] if pr["free_tier"].get("units_per_month") is not None else pr["free_tier"]["units_per_hour"]   # AC24 keyless
            sem = None; gmeta = {}; last_read = None
            if fg:   # AWI-PROVIDER-CONTEXTDEV-001: attempts guard per grant-anchor window, max(meter, counter), max_concurrent semaphore; free tier only
                if fg.get("paid_tier") is not False: raise CapViolation(f"{pid}: free_tier_guard.paid_tier must be false (no paid path)")
                quota = min(quota, fg["quota_credits"]); sem = guard.make_semaphore(fg["max_concurrent"])
            lim = guard.make_limiter(fg, clock, sleep) if fg else None   # keenable AC19: <= 9/s and <= 899 per rolling hour, client side
            # keenable AC23 + Keenable R5 (3): raw results count as publishable only when publish_raw_results is exactly bool True; absent or any other value = False
            no_raw = (pid in guard.FREE_ONLY or "publish_raw_results" in (fg or {})) and (fg or {}).get("publish_raw_results") is not True
            status = {"status": "ran", "calls": 0, "data_calls": 0}; pending = {}; billed = 0; short = []; recs = []; run429 = 0
            budget = (cfg.get("run_budget_s") or {}).get(pid); t_start = clock()
            mb = (cfg.get("meter_budget") or {}).get(pid); mb = mb if mb and mb.get("pool") == pool else None; m0 = None
            for set_name, s in limit_sets(sets, (limits or {}).get(pid)).items():
                if set_kind(set_name) != kind: continue
                out_f = Path(runs_dir) / run_date / pid / f"{set_name}.jsonl"; fh = pending.setdefault(out_f, []); reached = 0
                for item in s["rows"]:
                    if only_ids is not None and not (only_ids(pid, item["id"]) if callable(only_ids) else item["id"] in only_ids): continue
                    line = {"ts": (now_fn() if now_fn else dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds"), "provider": pid, "set": set_name,
                            "set_sha": s["sha"], "set_rows": len(s["rows"]), **({"sample_of": s["sample_of"]} if "sample_of" in s else {}), "set_flags": s.get("flags", []), "item_id": item["id"], "request": _request(kind, item), "offset_h": offset_h, "mock": mock}
                    if item.get("class") == "anti_bot" or item.get("category") == "anti_bot":
                        t = tos.get(pid) or {}
                        if not anti_bot_allowed(t):
                            ab = (t.get("anti_bot_scraping") or {})
                            reached += 1
                            fh.append(json.dumps(line | {"status": "skipped_tos", "pass": None, "billed_units": 0, "response_hash": None, "latency_ms": None,
                                                        "reason": f"tos_matrix: allowed={t.get('allowed')} anti_bot_scraping={ab.get('status')} checked_at={t.get('checked_at')} terms={t.get('terms_url')}"}) + "\n")
                            continue
                    try: est, req = call_estimate(pid, kind, item, pr, cfg, fg, mb, pool)   # the same estimate check_run() projects
                    except UnknownCost as e:
                        status = stale("metered_budget" if mb else "budget", status, f"unknown per-call cost ({e}); failing closed"); break
                    if mb:
                        mv = read_meter(meters, pid)
                        if mv is None:
                            status = stale("metered_budget", status, "usage meter unreadable; failing closed (rule 6)"); break
                        lv = ledger.used(pool) * mb["ledger_unit_usd"]; m0 = m0 or (mv, lv); g = max(mv, lv)
                        if g >= mb["stop_usd"] or g + est * mb["ledger_unit_usd"] > mb["stop_usd"]:
                            status = stale("metered_budget", status, f"max(meter ${mv:.4f}, ledger ${lv:.4f}) + next call would reach the ${mb['stop_usd']:.2f} stop (cap ${mb['cap_usd']:.2f})"); break
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
                    sq = (cfg.get("shared_quota") or {}).get(pid)
                    if sq:   # owner call 3: before every call, the shared balance must stay at or above floor_credits after it
                        sv = read_meter(meters, pid)
                        if sv is None:   # R45.3: unreadable balance = skipped_balance_unknown (no call); the partial run stays unpublished (stale_quota)
                            status = stale("quota", status, "shared-quota meter unreadable; failing closed") | {"skip": SKIPPED_BALANCE_UNKNOWN}
                            skip_row({"status": SKIPPED_BALANCE_UNKNOWN, "provider": pid, "category": kind, "where": f"before call {item['id']} ({set_name})", "reason": "shared-quota meter unreadable; failing closed"}); break
                        wc = worst_case_units(sq, kind, est)   # owner call 6:31 AM ET: the next call's worst-case units, never its typical cost
                        if wc is None:
                            status = stale("quota", status, f"shared_quota.{pid}.worst_case_units_per_call has no {kind} entry; failing closed"); break
                        if _dec(sv) - wc < _dec(sq["floor_credits"]):
                            floor_hits[pid] = {"reached": True, "balance_credits": str(sv), "floor_credits": str(sq["floor_credits"]), "planned_units": _s(wc), "where": f"before call {item['id']} ({set_name})"}
                            print(floor_line(pid, floor_hits[pid]))
                            why = f"{pid}_floor_reached: shared balance {sv} credits - next call worst case {_s(wc)} would go below the floor of {sq['floor_credits']} kept for Fred's other tools"
                            status = stale("quota", status, why) | {"skip": SKIPPED_FLOOR}   # R45.3: stop this provider's calls; the others continue
                            skip_row({"status": SKIPPED_FLOOR, "provider": pid, "category": kind, "balance_credits": str(sv), "floor_credits": str(sq["floor_credits"]), "where": floor_hits[pid]["where"], "reason": why}); break
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
                        net = False; tmo = False
                        try: out = guard.guarded_call(sem, fn, key, item) if sem else fn(key, item)
                        except Exception as e:
                            out = {"error": f"{type(e).__name__}: {_http_code(e)}{e}"}; tmo = bool(mb) and is_timeout_error(e); net = is_network_error(e) and not tmo   # metered (Apify) timeouts: a fail (AC4)
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
                            units = 0 if net or (auth and auth.group(1) == "1") or (tmo and not mb) else out.get("billed_units", est)   # metered timeout: assume billed (est)
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
                        rec["cost_estimate_units"] = est; rec["cumulative_spend"] = ledger.used(pool)   # AC8: units this month after this call
                        if mb: rec["cumulative_usd"] = round(ledger.used(pool) * mb["ledger_unit_usd"], 6)
                        if tmo: rec["timeout"] = True   # AC4: a fail, scored; reason carries "TimeoutError"
                        cum_usd = max(g + units * mb["ledger_unit_usd"], ledger.used(pool) * mb["ledger_unit_usd"]) if mb else None   # pre-call max(meter, ledger) + this bill
                        if mb and (units > est or cum_usd > mb["stop_usd"]):   # billed more than the guard assumed, or past the stop
                            ov = {"provider": pid, "item_id": item["id"], "set": set_name, "ts": line["ts"], "billed_units": units, "estimate_units": est,
                                  "cumulative_usd": round(cum_usd, 6), "stop_usd": mb["stop_usd"], "over_cap": cum_usd > mb["stop_usd"], "alert": "chief"}
                            rec["cap_overrun"] = ov; status.setdefault("cap_overrun", []).append(ov)
                            of = Path(spend_dir) / f"{pid}-cap-overrun-{run_date[:7]}.json"
                            olog = json.loads(of.read_text()) if of.exists() else {"provider": pid, "month": run_date[:7], "records": []}
                            olog["records"].append(ov); _atomic_write(of, json.dumps(olog, indent=1, sort_keys=True))
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
                        soh = cfg.get("stop_on_http") or {}
                        err = str(out.get("error", ""))
                        if soh.get("402") and (re.match(r"\S+: HTTP 402\b", err) or PAID_BODY.search(err)):   # out of credit / plan limit: stop now
                            status = stale(soh["402"], status, f"HTTP 402 on {item['id']} ({set_name})" if "HTTP 402" in err else f"payment/usage-limit error on {item['id']} ({set_name}): {PAID_BODY.search(err).group(1)}")
                            halt = True; break
                        if not tmo: run429 = run429 + 1 if is_rate_limited(rec) else 0   # AC6: a timeout neither resets nor advances the streak
                        if soh.get("429_consecutive") and run429 >= soh["429_consecutive"]:   # 429 storm: stop now
                            status = stale("rate_limited", status, f"{run429} consecutive HTTP 429 answers, last on {item['id']} ({set_name})")
                            halt = True; break
                        if is_rate_limited(rec) and soh.get("429_consecutive") and not retry:   # AC6: wait Retry-After (bounded) before the next call (the contextdev F5 retry waits on its own)
                            ra429 = RETRY_AFTER.search(err); wait = float(ra429.group(1)) if ra429 else soh.get("retry_after_default_s", 0)
                            time.sleep(min(wait, soh.get("retry_after_max_s", 60)))
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
            if not status["data_calls"] and not billed and not (fg and status["calls"]) and not any(r.get("timeout") for r in recs):   # nothing returned, billed or timed out: no run lines, no ledger entry (a guarded provider's attempts are always logged)
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
            spend = {"run_spend_units": round(billed, 6), "cumulative_spend_units": round(ledger.used(pool), 6)}   # AC8
            if mb: spend |= {"run_spend_usd": round(billed * mb["ledger_unit_usd"], 6), "cumulative_spend_usd": round(ledger.used(pool) * mb["ledger_unit_usd"], 6)}
            summary["providers"].append(base | status | {"quota_used": ledger.used(pool), "quota": quota} | gmeta | spend | gap)
            if status["status"] in PROVIDER_STOPS: pstop = status
    if not any(p.get("data_calls") for p in summary["providers"]) and json.dumps(ledger.d, sort_keys=True) == used_start:
        return summary   # nothing returned data and nothing billed: leave ledger and runs untouched
    ledger.save()
    if ledger.d.get("over_free_units", 0) > 0: raise CapViolation("ledger shows usage past the free tier (billed $ > 0)")
    sp = Path(runs_dir) / run_date / f"_run-offset{offset_h}.json"; sp.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(sp, json.dumps(summary, indent=1)); return summary
