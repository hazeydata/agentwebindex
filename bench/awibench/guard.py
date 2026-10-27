"""context.dev free-tier credit guard (AWI-PROVIDER-CONTEXTDEV-001 spec rev 4; Side Hustle CEO R1-R9, fact desk F1-F7, 2026-10-25).

- live_allowed: R9 + R22 gate (R22 extends R21 (1); follows R9; R26: the runner calls it for every live-gated provider whatever the
  config holds, so a missing live_run_allowed key, a null record or a missing free_tier_guard block means no live call). No call unless config live_run_allowed is True AND the config vendor_confirmation passes gates.record_error AND the tos_matrix entry exists with vendor_confirmation_required True AND its own record, if set, passes gates.record_error.
- anchor_window_start: the free tier refills on the org's grant-anchor day (F1), not on the 1st. The counter file is keyed by the
  window-start date; an unknown anchor day (null) raises AnchorNotConfigured and the runner uses one counter that never resets.
- CreditGuard: attempts counter in <dir>/contextdev-<window-start>.json. EVERY attempt adds its credit cost (F4), billed or not (R1);
  `billed` is kept beside it. The run stops at exactly stop_at (900). A missing-but-expected, corrupt, unreadable or locked counter
  raises GuardUnreadable and the runner fails closed (stale_budget).
- contextdev_credits: F4 credit cost of one call. contextdev_billed: F5 billing per HTTP status (cost math only).
- make_semaphore / guarded_call: every contextdev adapter call runs inside a max_concurrent (1) BoundedSemaphore (F2).

Keenable (AWI-PROVIDER-KEENABLE-001 K1, R1-R3; built with mocks in K1, open since the K2 flip PR #36, squash-merged 2026-10-27):
the committed config and tos_matrix carry live_run_allowed True and the #36 vendor record, so live_allowed is True for keenable.
It is keyless (KEYLESS) and publishable (not in publish_hold; AWI-KEENABLE-HOLD cancelled, Lucille 2, 1:33 AM ET Oct 27,
agent-asserted), so any harness run calls it. The only thing stopping scheduled live calls is the benchmark workflow being
disabled_manually in the repo's Actions settings (not in code). Its re-enable to run live on schedule within the caps is
approved (spec R40), sequenced after paper desk R25 reaches Kitty. No live keenable call has been made yet
as of this comment. Live runs are covered by Fred's Oct 26 go within spend caps (R24, R39); there is no per-run go. Any run
over a cap or adding a new provider stops and goes back to Lucille 2.
- live_allowed(cfg, tos, pid) is the one live gate for every guarded provider; FREE_ONLY providers (keenable) also need
  free_tier_guard.<pid>.paid_usage_allowed to be bool False (missing or non-bool fails closed, R1).
- request_opts / credits / billed dispatch per provider id (contextdev keeps its F4/F5 rules; keenable: 1 attempt per call, 0 billed:
  the keyless public endpoint is unmetered).
- paid_stop: a keenable HTTP 402, or any response carrying a usage/billing block, means billed usage or no free allowance: stop all
  calls (stopped_paid_usage). rate_stop: a 429 is retried at most once after Retry-After, then the provider stops as rate_limited.
- RateLimiter: client-side limiter, serial, at most max_per_second per rolling second and fewer than max_per_hour+1 per rolling hour
  (config rate_limit: 9/s and 899/h, under the docs' 10/s and 1,000/h unauthenticated limits and the spec's 900/h budget).
- hour_window: keenable's call counter is keyed by the UTC hour (window "utc_hour"), so the persistent counter caps each hour too.
"""
import calendar, datetime as dt, fcntl, json, math, os, re, threading, time
from pathlib import Path
from .gates import REQUIRED_FLAG, vendor_confirmed

UNREADABLE = "credit counter unreadable; failing closed"
UNANCHORED = "unanchored"

class GuardUnreadable(RuntimeError):
    """The credit counter cannot be read, written or locked. The message is always UNREADABLE (detail: exception type name only)."""
    def __init__(self, detail=None): super().__init__(UNREADABLE); self.detail = detail

class AnchorNotConfigured(ValueError):
    """grant_anchor_day is null: the window start is unknown, so the counter must never reset."""

FREE_ONLY = ("keenable",)   # providers whose config must say paid_usage_allowed: false (R1)
KEYLESS = ("keenable",)   # AC18: keyless public endpoint, no env key read
STOP_STATUSES = ("stopped_paid_usage", "rate_limited")   # provider outcomes that, like stale_<reason>, are never published
LIVE_GATED = ("contextdev", "keenable")   # R26 (C07) + Keenable R5 (2): the runner consults live_allowed for these unconditionally

def live_allowed(cfg, tos, pid="contextdev"):
    """R9 + R22 (R22 extends R21 (1); follows R9). R26: called unconditionally for live-gated providers; a missing key or block is False: live calls only when ALL hold, else fail closed: config live_run_allowed is bool True; the config
    vendor_confirmation passes gates.record_error (gates.py:12, the one record validator; no truthiness check); the tos_matrix entry
    exists; its vendor_confirmation_required is True; and its own vendor_confirmation, if set, passes gates.record_error.
    Both record checks go through gates.vendor_confirmed, which only wraps gates.record_error.
    Keenable K1 AC3 + R1: the same rules for any provider id; a FREE_ONLY provider also needs paid_usage_allowed bool False."""
    g = ((cfg or {}).get("free_tier_guard") or {}).get(pid) or {}
    if pid in FREE_ONLY and g.get("paid_usage_allowed") is not False: return False
    e = (tos or {}).get(pid)
    # vendor_confirmed({flag: True, record}) is exactly gates.record_error(record) is None (AC24d: one function)
    return (g.get("live_run_allowed") is True and vendor_confirmed({REQUIRED_FLAG: True, "vendor_confirmation": g.get("vendor_confirmation")})
            and type(e) is dict and e.get(REQUIRED_FLAG) is True
            and (e.get("vendor_confirmation") is None or vendor_confirmed(e)))

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
        self.window = UNANCHORED if window_start is None else window_start if type(window_start) is str else window_start.isoformat()   # str: keenable UTC-hour window
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
    def allows(self, next_units, meter=None):
        """The one stop check: max(vendor meter, attempts counter) + the next call's estimated credits must stay <= stop_at (the run stops at exactly stop_at)."""
        return max(meter or 0, self.used()) + next_units <= self.stop_at
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

# ---- per-provider dispatch (contextdev functions above stay the contextdev rules; looked up at call time so tests may patch them)
def request_opts(pid, kind, item):
    if pid == "contextdev": return contextdev_request(kind, item)
    if pid == "keenable": return keenable_request(kind, item)
    raise ValueError(f"no guarded request rule for {pid!r}")

def credits(pid, kind, request, n_results):
    if pid == "contextdev": return contextdev_credits(kind, request, n_results)
    if pid == "keenable": return keenable_credits(kind, request, n_results)
    raise ValueError(f"no credit rule for {pid!r}")

def billed(pid, http_status, body, cr):
    if pid == "contextdev": return contextdev_billed(http_status, body, cr)
    if pid == "keenable": return 0   # keyless public endpoint: unmetered, no credits (docs credits.md: "Unauthenticated calls are unbilled")
    raise ValueError(f"no billing rule for {pid!r}")

def paid_stop(pid, http_status, out):
    """R1/AC20: the reason to stop all keenable calls (stopped_paid_usage), else None. HTTP 402 (credits exhausted / payment required),
    a payment-required text, or any usage block (keyless calls omit it, so its presence means metered usage) or billed units > 0."""
    if pid not in FREE_ONLY: return None
    out = out or {}
    if http_status == 402 or "payment required" in str(out.get("error", "")).lower(): return f"HTTP {http_status or 402} payment required / free allowance exhausted"
    if out.get("usage") is not None: return "response carried a usage (billing) block"
    if out.get("billed_units") not in (None, 0): return f"response reported billed units {out.get('billed_units')!r}"
    return None

def keenable_request(kind, item):
    """The only request shape: query only (default mode); search only."""
    if kind != "search": raise ValueError(f"keenable is search-only, not {kind!r}")
    return {}

def keenable_credits(kind, request, n_results):
    """One attempt per call (the counter caps calls per UTC hour)."""
    if kind != "search": raise ValueError(f"keenable is search-only, not {kind!r}")
    return 1

def hour_window(ts):
    """UTC hour of ts as 'YYYY-MM-DDTHH' (keenable counter window)."""
    return (ts.astimezone(dt.timezone.utc) if ts.tzinfo else ts).strftime("%Y-%m-%dT%H")

class RateLimiter:
    """Client-side limiter for one serial caller. acquire() sleeps (via the injected sleep) until a call fits in max_per_second per
    rolling second, then returns True; it returns False, without sleeping, when the call would be the (max_per_hour+1)-th in the
    rolling hour (the run then stops before the vendor's hourly cap). Times are tracked virtually (now + slept), so a fake sleep works."""
    def __init__(self, max_per_second, max_per_hour, clock=time.monotonic, sleep=time.sleep):
        for v in (max_per_second, max_per_hour):
            if type(v) is not int or v < 1: raise ValueError("rate limits must be positive ints")
        self.ps, self.ph, self.clock, self.sleep, self.t, self.skew = max_per_second, max_per_hour, clock, sleep, [], 0.0
    def acquire(self):
        now = self.clock() + self.skew
        self.t = [x for x in self.t if x > now - 3600]
        if len(self.t) >= self.ph: return False
        recent = [x for x in self.t if x > now - 1.0]
        if len(recent) >= self.ps:
            wait = recent[-self.ps] + 1.0 - now
            if wait > 0: self.sleep(wait); self.skew += wait; now += wait
        self.t.append(now); return True

def make_limiter(fg, clock=time.monotonic, sleep=time.sleep):
    rl = (fg or {}).get("rate_limit")
    return None if rl is None else RateLimiter(rl["max_per_second"], rl["max_per_hour"], clock, sleep)
