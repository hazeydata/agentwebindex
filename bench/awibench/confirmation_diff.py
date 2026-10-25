"""AWI-PROVIDER-CONTEXTDEV-001 AC24e: a PR that records (or changes) a vendor confirmation must be a separate, data-and-docs-only PR.

check(changed_files, diff_text) -> list of problems; empty = fine. Fails when the diff changes any `vendor_confirmation` value (or the
`live_run_allowed` flag that goes with it, or a field inside a tos_matrix record) AND also changes code.
Code = any changed file that is not a doc (*.md, *.txt, docs/), not one of the two data files that hold the record, and not under bench/tests/
(the flip PR must update the tests that assert the committed null).

CLI: python -m awibench.confirmation_diff [BASE]    (default BASE origin/main; diffs BASE...HEAD; exit 1 on problems)
"""
import re, subprocess, sys
from pathlib import Path

DATA_FILES = {"bench/tos_matrix.yaml", "bench/config.yaml"}
KEY = re.compile(r'^[+-]\s*"?(vendor_confirmation|live_run_allowed)"?\s*:')
RECORD_KEY = re.compile(r'^[+-]\s*"?(date|sender|evidence_path|recorded_pr)"?\s*:')

def is_doc(f): return f.endswith((".md", ".txt")) or f.startswith("docs/")

def is_code(f): return not is_doc(f) and f not in DATA_FILES and not f.startswith("bench/tests/")

def changed_confirmation(diff_text):
    """True if any +/- line changes a vendor_confirmation key (config.yaml / tos_matrix.yaml) or a field of a tos_matrix record."""
    cur = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "): cur = line[4:].removeprefix("b/"); continue
        if line.startswith("--- ") or not line[:1] in "+-": continue
        if cur in DATA_FILES and (KEY.match(line) or (cur == "bench/tos_matrix.yaml" and RECORD_KEY.match(line))): return True
    return False

def check(changed_files, diff_text):
    if not changed_confirmation(diff_text): return []
    code = sorted(f for f in changed_files if is_code(f))
    return [f"vendor_confirmation changed together with code changes ({', '.join(code)}): record the vendor reply in a separate data/docs-only PR"] if code else []

def main(argv=None):
    base = (argv if argv is not None else sys.argv[1:] or ["origin/main"])[0]
    git = lambda *a: subprocess.run(["git", "-C", str(Path(__file__).resolve().parents[2]), *a], capture_output=True, text=True, check=True).stdout
    probs = check(git("diff", "--name-only", f"{base}...HEAD").split(), git("diff", "-U0", f"{base}...HEAD"))
    for p in probs: print("FAIL:", p)
    return 1 if probs else 0

if __name__ == "__main__": sys.exit(main())
