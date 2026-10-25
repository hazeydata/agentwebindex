"""AWI-PROVIDER-CONTEXTDEV-001 R10: the ONE vendor-confirmation publish gate. Nothing else in the repo may read tos_matrix.yaml's
`vendor_confirmation_required` / `vendor_confirmation` fields; tables.build, collectors/benchmark.py, the runner and the live-run check all import this."""
def _text(v): return isinstance(v, str) and v.strip() != ""

def vendor_confirmed(tos_entry):
    """True if the entry does not require vendor confirmation; else True only when vendor_confirmation holds a date, a sender and a quote or evidence_path."""
    e = tos_entry or {}
    if e.get("vendor_confirmation_required") is not True: return True
    c = e.get("vendor_confirmation")
    return isinstance(c, dict) and _text(c.get("date")) and _text(c.get("sender")) and (_text(c.get("quote")) or _text(c.get("evidence_path")))

def publishable(pid, pricing, tos):
    """vendor_confirmed(tos[pid]) and the provider's other gates (AC23 P0/P1 for contextdev)."""
    if not vendor_confirmed((tos or {}).get(pid)): return False
    if pid == "contextdev":
        from .tables import publish_gate
        return publish_gate(pid, pricing, tos)[0]
    return True
