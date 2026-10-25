"""AWI-PROVIDER-CONTEXTDEV-001 R10: the ONE vendor-confirmation publish gate. Nothing else in the repo may read tos_matrix.yaml's
`vendor_confirmation_required` / `vendor_confirmation` fields; tables.build, collectors/benchmark.py, the runner and the live-run check all import this."""
REQUIRED_FLAG = "vendor_confirmation_required"   # the one spelling; confirmation_diff (AC24h) imports it to guard the flag itself

import datetime, re

RECORD_KEYS = ("date", "sender", "quote", "evidence_path", "recorded_pr")   # spec line 95 + R19: exactly these keys
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

def _text(v): return type(v) is str and v.strip() != ""

def record_error(c):
    """R19: the ONE vendor_confirmation record validator (vendor_confirmed() and confirmation_diff's CI schema check both call it).
    None if `c` is a valid record, else the reason. A valid record is a mapping with exactly the keys date, sender, quote,
    evidence_path and recorded_pr (no extra keys); date is a quoted YYYY-MM-DD string that is a real calendar date (an unquoted
    YAML date fails); sender is a non-empty string; recorded_pr is a non-empty https:// URL; quote and evidence_path are each a
    string or null, and at least one is a non-empty string. Any other type fails."""
    if type(c) is not dict: return f"must be a record mapping, got {type(c).__name__}"
    keys = set(c)
    if keys != set(RECORD_KEYS):
        miss, extra = [k for k in RECORD_KEYS if k not in keys], sorted(map(repr, keys - set(RECORD_KEYS)))
        return "record keys must be exactly " + ", ".join(RECORD_KEYS) + (f"; missing {', '.join(miss)}" if miss else "") + (f"; extra {', '.join(extra)}" if extra else "")
    errs = []
    d = c["date"]
    if type(d) is not str or not _DATE.fullmatch(d): errs.append("date must be a quoted YYYY-MM-DD string")
    else:
        try: datetime.date.fromisoformat(d)
        except ValueError: errs.append("date is not a real calendar date")
    if not _text(c["sender"]): errs.append("sender must be a non-empty string")
    pr = c["recorded_pr"]
    if not (type(pr) is str and pr.startswith("https://") and len(pr) > len("https://") and not any(ch.isspace() for ch in pr)):
        errs.append("recorded_pr must be a non-empty https:// URL")
    for k in ("quote", "evidence_path"):
        if c[k] is not None and type(c[k]) is not str: errs.append(f"{k} must be a string or null")
    if not (_text(c["quote"]) or _text(c["evidence_path"])): errs.append("quote and/or evidence_path must be a non-empty string")
    return "; ".join(errs) or None

def vendor_confirmed(tos_entry):
    """True if the entry does not require vendor confirmation; else True only when vendor_confirmation passes record_error()."""
    e = tos_entry or {}
    if e.get(REQUIRED_FLAG) is not True: return True
    return record_error(e.get("vendor_confirmation")) is None

def publishable(pid, pricing, tos):
    """vendor_confirmed(tos[pid]) and the provider's other gates (AC23 P0/P1 for contextdev)."""
    if not vendor_confirmed((tos or {}).get(pid)): return False
    if pid == "contextdev":
        from .tables import publish_gate
        return publish_gate(pid, pricing, tos)[0]
    return True
