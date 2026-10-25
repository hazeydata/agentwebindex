"""context.dev free-tier credit guard (AWI-PROVIDER-CONTEXTDEV-001 rev 3; Side Hustle CEO R1-R9, fact desk F1-F7, 2026-10-25).

- live_allowed: R9 gate. No call unless config free_tier_guard.<pid>.live_run_allowed is true AND vendor_confirmation is set AND gates.vendor_confirmed(tos_matrix entry) (R10).
- anchor_window_start: the free tier refills on the org's grant-anchor day (F1), not on the 1st. The counter file is keyed by the
  window-start date; an unknown anchor day (null) raises AnchorNotConfigured and the runner uses one counter that never resets.
- CreditGuard: attempts counter in <dir>/contextdev-<window-start>.json. EVERY attempt adds its credit cost (F4), billed or not (R1);
  `billed` is kept beside it. The run stops at exactly stop_at (900). A missing-but-expected, corrupt, unreadable or locked counter
  raises GuardUnreadable and the runner fails closed (stale_budget).
- contextdev_credits: F4 credit cost of one call. contextdev_billed: F5 billing per HTTP status (cost math only).
- make_semaphore / guarded_call: every contextdev adapter call runs inside a max_concurrent (1) BoundedSemaphore (F2).
"""
import calendar, datetime as dt, fcntl, json, math, os, re, threading, time
from pathlib import Path
from .gates import vendor_confirmed

UNREADABLE = "credit counter unreadable; failing closed"
UNANCHORED = "unanchored"

class GuardUnreadable(RuntimeError):
    """The credit counter cannot be read, written or locked. The message is always UNREADABLE (detail: exception type name only)."""
    def __init__(self, detail=None): super().__init__(UNREADABLE); self.detail = detail

class AnchorNotConfigured(ValueError):
    """grant_anchor_day is null: the window start is unknown, so the counter must never reset."""

def live_allowed(cfg, tos, pid="contextdev"):
    """R9 + R10: live calls only when config live_run_allowed is True, the config vendor_confirmation flag record is set (R9 record only),
    AND the tos_matrix confirmation record passes gates.vendor_confirmed (the only place a confirmation record is validated)."""
    g = ((cfg or {}).get("free_tier_guard") or {}).get(pid) or {}
    return g.get("live_run_allowed") is True and bool(g.get("vendor_confirmation")) and pid in (tos or {}) and vendor_confirmed(tos[pid])   # no tos entry -> closed

def _clamp(y, m, day): return dt.date(y, m, min(day, calendar.monthrange(y, m)[1]))

def anchor_window_start(ts, anchor_day):
    """Most recent anchor day <= ts (UTC date), clamped to the month length (anchor 31 -> Nov 30, Feb 28). None raises (fail closed)."""
    if anchor_day is None: raise AnchorNotConfigured("grant anchor not configured")
    d = ts.astimezone(dt.timezone.utc).date() if ts.tzinfo else ts.date()
    cand = _clamp(d.year, d.month, anchor_day)
    if cand <= d: return cand
    y, m = (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
    return _clamp(y, m, anchor_day)

def _count_ok(v): return type(v) is int and v >= 0

def _valid(d, pid, window):
    return isinstance(d, dict) and d.get("provider") == pid and d.get("window") == window and _count_ok(d.get("attempted")) and _count_ok(d.get("billed"))

class CreditGuard:
    """Holds an exclusive fcntl lock from construction until release(); the runner constructs one per attempt.
    window_start: the grant-anchor window start date, or None (anchor unknown: one counter, never reset)."""
    def __init__(self, spend_dir, pid, window_start, stop_at, lock_timeout=30.0, poll=0.05):
        d = Path(spend_dir); self.pid, self.stop_at, self.lock = pid, stop_at, None
        self.window = UNANCHORED if window_start is None else window_start.isoformat()
        if not d.is_dir() or not os.access(d, os.R_OK | os.W_OK | os.X_OK): raise GuardUnreadable("spend dir")
        self.path = d / f"{pid}-{self.window}.json"
        try: self.lock = open(d / f".{pid}.lock", "a")
        except OSError as e: raise GuardUnreadable(type(e).__name__) from None
        deadline = time.monotonic() + lock_timeout
        while True:
            try: fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB); break
            except OSError:
                if time.monotonic() >= deadline: self.release(); raise GuardUnreadable("lock timeout") from None
                time.sleep(poll)
        try:
            if self.path.exists():
                self.d = json.loads(self.path.read_text())
                if not _valid(self.d, pid, self.window): raise ValueError("bad fields")
            else:
                later = [f for f in d.glob(f"{pid}-*.json") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", f.stem[len(pid) + 1:]) and f.stem[len(pid) + 1:] > self.window]
                if window_start is not None and later: raise ValueError("a later window file exists")   # an earlier window than one already seen: do not restart at 0
                self.d = {"provider": pid, "window": self.window, "attempted": 0, "billed": 0}; self._write()
        except (OSError, ValueError) as e:
            self.release(); raise GuardUnreadable(type(e).__name__) from None
    def _write(self):
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w") as f: f.write(json.dumps(self.d, indent=1, sort_keys=True)); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, self.path)
    def used(self): return self.d["attempted"]
    def allows(self, next_units): return self.used() + next_units <= self.stop_at
    def record(self, attempted, billed):
        self.d = self.d | {"attempted": self.d["attempted"] + attempted, "billed": self.d["billed"] + billed}
        try: self._write()
        except OSError as e: raise GuardUnreadable(type(e).__name__) from None
    def release(self):
        if self.lock: fcntl.flock(self.lock, fcntl.LOCK_UN); self.lock.close(); self.lock = None

def contextdev_request(kind, item):
    """Request options the adapters send (single source for the pre-call credit estimate): search asks for 10 results, no markdown/highlights."""
    return {"max_results": 10} if kind == "search" else {}

def contextdev_credits(kind, request, n_results):
    """F4: scrape 1 (2 with browser actions); search ceil(n/10), +ceil(n/10) with markdown, +ceil(n/10) with highlights.
    n_results None = the requested count (pre-call); otherwise the returned count."""
    request = request or {}
    if kind == "scrape": return 2 if request.get("browser_actions") else 1
    if kind != "search": raise ValueError(f"no context.dev credit rule for {kind!r}")
    n = request.get("max_results", 10) if n_results is None else n_results
    return math.ceil(n / 10) * (1 + bool(request.get("markdown")) + bool(request.get("highlights")))

def error_body(text):
    """The part of a vendor error body the guard cares about: {'error_code': 'USAGE_EXCEEDED'} when the text carries it, else {}."""
    return {"error_code": "USAGE_EXCEEDED"} if "USAGE_EXCEEDED" in str(text or "") else {}

def is_quota_stop(http_status, body):
    """F5: HTTP 402 or 401 with USAGE_EXCEEDED means out of credits (never an auth failure)."""
    return http_status in (401, 402) and (body or {}).get("error_code") == "USAGE_EXCEEDED"

def contextdev_billed(http_status, body, credits):
    """F5, cost math only (the guard counts every attempt): 2xx and 404 bill `credits`; validation 400/422, 401, 403, 408, 429, 5xx, no response
    and 402/401 USAGE_EXCEEDED bill 0. Any other status bills 0 (docs: only successful work and a target 404 are charged)."""
    if http_status is None or is_quota_stop(http_status, body): return 0
    return credits if 200 <= http_status < 300 or http_status == 404 else 0

def make_semaphore(n): return threading.BoundedSemaphore(n)

def guarded_call(sem, fn, key, item):
    """fn(key, item) inside the provider's concurrency semaphore (acquired before, released after, also on error)."""
    sem.acquire()
    try: return fn(key, item)
    finally: sem.release()

def meter_gap(spend_dir, pid, run_date, meter, counter):
    """Append meter-vs-counter to <spend_dir>/<pid>-meter-YYYY-MM.json. Returns the gap fields (meter None -> counter only)."""
    gap = {"meter_credits": meter, "counter_credits": counter, "meter_minus_counter": None if meter is None else meter - counter}
    try:
        f = Path(spend_dir) / f"{pid}-meter-{run_date[:7]}.json"
        log = json.loads(f.read_text()) if f.exists() else {"provider": pid, "month": run_date[:7], "runs": []}
        log["runs"].append({"run_date": run_date} | gap); f.write_text(json.dumps(log, indent=1, sort_keys=True))
    except (OSError, ValueError): pass
    return gap
