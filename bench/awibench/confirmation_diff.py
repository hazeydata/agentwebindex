"""AWI-PROVIDER-CONTEXTDEV-001 AC24e: a PR that records (or changes) a vendor confirmation must be a separate, data-and-docs-only PR.

check(changed_files, diff_text) -> list of problems; empty = fine. When the diff changes any `vendor_confirmation` value (or the
`live_run_allowed` flag that goes with it, or a field inside a tos_matrix record), the PR must be FLIP-ONLY (R13). Flip allowlist:
- bench/tos_matrix.yaml: only `vendor_confirmation` lines and its record fields (date, sender, quote, evidence_path, recorded_pr), plus bare braces
- bench/config.yaml: only `live_run_allowed` and `vendor_confirmation` lines (and the record fields / braces inside it)
- the vendor-reply evidence file and cycle docs: `[agentwebindex-meta/]cycles/<cycle>/evidence/<file>`, `.../summary.md`, `.../decisions.md`
Anything else (code, tests, pricing.yaml, providers.json, README or any other doc, other fields in the two yaml files) is rejected.
A price taken from the vendor reply lands in a separate PR.

Bootstrap allowance (narrow): an ADDED line that sets one of these keys to the closed default (`vendor_confirmation: null`,
`live_run_allowed: false`, or a record field `null`) is not a confirmation change, but only while nothing is confirmed after the diff
(`confirmed=False`: no tos_matrix or config free_tier_guard entry holds any non-null vendor_confirmation, complete or not, and no
config entry has live_run_allowed true). This
lets the PR that introduces a gated provider (its entries start closed) carry code. Any removed line, any non-null/non-false value, or any
diff that leaves something confirmed still counts.

CLI: python -m awibench.confirmation_diff [BASE]    (default BASE origin/main; diffs BASE...HEAD; exit 1 on problems)
"""
import re, subprocess, sys
from pathlib import Path

DATA_FILES = {"bench/tos_matrix.yaml", "bench/config.yaml"}
KEY = re.compile(r'^[+-]\s*"?(vendor_confirmation|live_run_allowed)"?\s*:')
RECORD_KEY = re.compile(r'^[+-]\s*"?(date|sender|evidence_path|recorded_pr)"?\s*:')
FLIP_FIELD = re.compile(r'^[+-]\s*"?(date|sender|quote|evidence_path|recorded_pr)"?\s*:')   # allowlist only (detection unchanged)

FLIP_PATH = re.compile(r"^(agentwebindex-meta/)?cycles/[^/]+/(summary\.md|decisions\.md|evidence/[^/]+)$")
BRACES = re.compile(r'^[+-]\s*[{}\[\]]?\s*[}\]]?\s*,?\s*$')

def flip_allowed_path(f): return f in DATA_FILES or bool(FLIP_PATH.match(f))

def bad_data_lines(diff_text):
    """Changed lines in the two data files that are not a confirmation key, a record field or a bare brace."""
    cur, bad = None, []
    for line in diff_text.splitlines():
        if line.startswith("+++ "): cur = line[4:].removeprefix("b/"); continue
        if line.startswith("--- ") or not line[:1] in "+-" or cur not in DATA_FILES: continue
        if KEY.match(line) or FLIP_FIELD.match(line) or BRACES.match(line): continue
        bad.append(f"{cur}: {line[:80]}")
    return bad

CLOSED_DEFAULT = re.compile(r'^\+[^:]*:\s*(null|false)\s*,?\s*$')

def changed_confirmation(diff_text, confirmed=True):
    """True if any +/- line changes a vendor_confirmation key (config.yaml / tos_matrix.yaml) or a field of a tos_matrix record.
    confirmed=False enables the bootstrap allowance: added lines setting the closed default (null / false) are ignored."""
    cur = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "): cur = line[4:].removeprefix("b/"); continue
        if line.startswith("--- ") or not line[:1] in "+-": continue
        if cur in DATA_FILES and (KEY.match(line) or (cur == "bench/tos_matrix.yaml" and RECORD_KEY.match(line))):
            if not confirmed and CLOSED_DEFAULT.match(line): continue
            return True
    return False

def check(changed_files, diff_text, confirmed=True):
    if not changed_confirmation(diff_text, confirmed): return []
    extra = sorted(f for f in changed_files if not flip_allowed_path(f)) + bad_data_lines(diff_text)
    return [f"confirmation flip is not flip-only (R13): {'; '.join(extra)}. Record the vendor reply alone (confirmation fields, evidence file, summary.md/decisions.md); everything else goes in its own PR"] if extra else []

def head_confirmed(root):
    """True if anything is (even partly) confirmed in the checked-out tree: any tos_matrix or config free_tier_guard entry with a non-null
    vendor_confirmation (stricter than gates.vendor_confirmed: an incomplete record also counts), or live_run_allowed true.
    Unreadable files count as confirmed (no allowance; fail closed)."""
    from .util import load_yaml
    try:
        tos = (load_yaml(root / "bench/tos_matrix.yaml") or {}).get("providers") or {}
        cfg = load_yaml(root / "bench/config.yaml") or {}
    except Exception: return True
    entries = [e for e in list(tos.values()) + list((cfg.get("free_tier_guard") or {}).values()) if isinstance(e, dict)]
    return any(e.get("vendor_confirmation") is not None or e.get("live_run_allowed") is True for e in entries)

def main(argv=None):
    base = (argv if argv is not None else sys.argv[1:] or ["origin/main"])[0]
    root = Path(__file__).resolve().parents[2]
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, check=True).stdout
    probs = check(git("diff", "--name-only", f"{base}...HEAD").split(), git("diff", "-U0", f"{base}...HEAD"), head_confirmed(root))
    for p in probs: print("FAIL:", p)
    return 1 if probs else 0

if __name__ == "__main__": sys.exit(main())
