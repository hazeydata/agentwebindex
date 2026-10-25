"""AWI-PROVIDER-CONTEXTDEV-001 AC24e (R13): a PR that records (or changes) a vendor confirmation must be FLIP-ONLY.

STRUCTURAL check: bench/tos_matrix.yaml and bench/config.yaml are parsed at the base and at the head, flattened to field paths, and
compared field by field (no line regexes).

A confirmation change is any changed field path at or under
- tos_matrix.yaml  providers.<pid>.vendor_confirmation           (every record field: date, sender, quote, evidence_path, recorded_pr, ...)
- config.yaml      free_tier_guard.<pid>.vendor_confirmation / .live_run_allowed
When there is one, the PR must be flip-only:
- every changed field in the two yaml files is a confirmation field of ONE provider (the flipped provider); any other changed key, in any
  record or block (e.g. providers.contextdev.benchmarking.quote, a nested config date, another provider's fields), is rejected
- every other changed file is the vendor-reply evidence file or a cycle doc: `[agentwebindex-meta/]cycles/<cycle>/evidence/<file>` (one path
  segment, no subdirectories), `.../summary.md`, `.../decisions.md`. Code, tests, pricing.yaml, providers.json, README and every other
  path is rejected. A price taken from the vendor reply lands in a separate PR.

Bootstrap allowance (narrow): a confirmation field that is NEW at the head (absent at the base) and holds the closed default (null or
false) is not a confirmation change, but only while nothing is confirmed at the head (head_confirmed False: no tos_matrix or config
free_tier_guard entry holds any non-null vendor_confirmation, complete or not, and no config entry has live_run_allowed true). This lets the
PR that introduces a gated provider carry code. A removed field, a changed value, or a head with anything confirmed still counts.

AC24h (any PR, flip or not): the gate itself may not be lowered. It fails when, between base and head, a tos_matrix provider that has
`gates.REQUIRED_FLAG` at the base loses it, has it changed from true to anything else (deleting or renaming the key fails whatever its base value: true, false or null), or the provider entry is deleted or renamed
(absent at head under the same id). There is no override: lowering the gate needs a separate Fred or CEO ruling.
R16: SQ2 rejects any change to PROTECTED files that exist at the base; SQ3 compares strictly on type and value.
R17 SQ-C (every PR, flip or not): schema types at the head. vendor_confirmation_required and live_run_allowed must be bool;
vendor_confirmation must be null or a record that passes gates.record_error (R19; spec line 95).

check(changed_files, base, head) -> list of problems (empty = fine); base/head = {"bench/tos_matrix.yaml": doc, "bench/config.yaml": doc}.
CLI: python -m awibench.confirmation_diff [BASE [HEAD]]    (defaults origin/main and HEAD; compares merge-base(BASE, HEAD) with HEAD; exit 1 on problems).
AWI_REPO_ROOT sets the repo root, so CI can run the BASE copies of this file and gates.py from a temp dir against the PR head
(R15, .github/workflows/confirmation-diff-base.yml). That base-judged job is authoritative; the advisory runs
(confirmation-diff.yml and run_tests.sh) use the PR's own copy of this file. All three use the same base (origin/<base branch>, the
base sha in the base-judged job) and diff from merge-base(BASE, HEAD), so commits that moved the base branch ahead are not counted.
"""
import json, os, re, subprocess, sys
from pathlib import Path

TOS, CFG = "bench/tos_matrix.yaml", "bench/config.yaml"
DATA_FILES = {TOS, CFG}
FLIP_PATH = re.compile(r"^(agentwebindex-meta/)?cycles/[^/\x00-\x1f]+/(summary\.md|decisions\.md|evidence/[^/\x00-\x1f]+)")   # cycles/<slug>/ only; always fullmatch (no trailing-newline slack)
MISSING = object()

def flip_allowed_path(f): return f in DATA_FILES or bool(FLIP_PATH.fullmatch(f))

def flatten(doc, prefix=()):
    """{path tuple: leaf}. Dicts are walked (an empty dict is a leaf); lists and scalars are leaves."""
    if isinstance(doc, dict) and doc:
        out = {}
        for k, v in doc.items(): out |= flatten(v, prefix + (str(k),))
        return out
    return {prefix: doc}

def same(a, b):
    """SQ3 (R16): strict on type and value. false vs 0, 'false' or null, and true vs 1 or 'true', all differ."""
    if a is MISSING or b is MISSING: return a is b
    return type(a) is type(b) and json.dumps(a, sort_keys=True, default=repr) == json.dumps(b, sort_keys=True, default=repr)

def changed_paths(base_doc, head_doc):
    """[(path, base leaf or MISSING, head leaf or MISSING)] for every field that differs."""
    b, h = flatten(base_doc or {}), flatten(head_doc or {})
    return [(p, b.get(p, MISSING), h.get(p, MISSING)) for p in sorted(set(b) | set(h)) if not same(b.get(p, MISSING), h.get(p, MISSING))]

def confirmation_pid(f, path):
    """The provider id if `path` in file f is a confirmation field, else None."""
    if f == TOS and len(path) >= 3 and path[0] == "providers" and path[2] == "vendor_confirmation": return path[1]
    if f == CFG and len(path) >= 3 and path[0] == "free_tier_guard" and path[2] in ("vendor_confirmation", "live_run_allowed"): return path[1]
    return None

def head_confirmed(head):
    """True if anything is (even partly) confirmed at the head: any tos_matrix or config free_tier_guard entry with a non-null
    vendor_confirmation (stricter than gates.vendor_confirmed: an incomplete record also counts), or live_run_allowed true.
    A missing or unparsable file (None) counts as confirmed (no allowance; fail closed)."""
    tos, cfg = head.get(TOS), head.get(CFG)
    if not isinstance(tos, dict) or not isinstance(cfg, dict): return True
    entries = [e for e in list((tos.get("providers") or {}).values()) + list((cfg.get("free_tier_guard") or {}).values()) if isinstance(e, dict)]
    return any(e.get("vendor_confirmation") is not None or e.get("live_run_allowed") is True for e in entries)

def gate_lowered(base, head):
    """AC24h: problems for every provider whose required-confirmation flag is dropped, lowered, or whose entry is deleted/renamed."""
    from .gates import REQUIRED_FLAG
    bp = ((base.get(TOS) or {}).get("providers") or {}) if isinstance(base.get(TOS), dict) else {}
    hp = ((head.get(TOS) or {}).get("providers") or {}) if isinstance(head.get(TOS), dict) else {}
    out = []
    for pid, e in sorted(bp.items()):
        if not isinstance(e, dict) or REQUIRED_FLAG not in e: continue
        h = hp.get(pid)
        if not isinstance(h, dict): out.append(f"{pid}: gated tos_matrix entry deleted or renamed")
        elif REQUIRED_FLAG not in h: out.append(f"{pid}: {REQUIRED_FLAG} deleted")
        elif e[REQUIRED_FLAG] is True and h[REQUIRED_FLAG] is not True: out.append(f"{pid}: {REQUIRED_FLAG} lowered from true")
        elif not same(e[REQUIRED_FLAG], h[REQUIRED_FLAG]) and h[REQUIRED_FLAG] is not True: out.append(f"{pid}: {REQUIRED_FLAG} changed ({e[REQUIRED_FLAG]!r} -> {h[REQUIRED_FLAG]!r}; strict, SQ3)")
    return [f"AC24h: the vendor-confirmation gate may not be lowered ({'; '.join(out)}); that needs a separate Fred or CEO ruling"] if out else []

PROTECTED = ("bench/awibench/gates.py", "bench/awibench/confirmation_diff.py",
             ".github/workflows/confirmation-diff.yml", ".github/workflows/confirmation-diff-base.yml")

def protected_touched(changed_files, base_has):
    """SQ2 (R16): the checker, gates.py and the confirmation workflows may not change once they exist at the base.
    base_has = the PROTECTED files present at the base (None = all of them: fail closed). A file the base lacks is not "changed"
    here; when the base lacks the checker itself, the base-judged job fails closed (SQ1, R17 SQ-A: no exemption, #27 included;
    #27 is judged by Kitty's fixtures, the mutants and Weatherman's SHIP at its exact head). After #27, any change needs a separate
    Fred or CEO ruling."""
    have = set(PROTECTED) if base_has is None else set(base_has)
    hit = sorted(f for f in changed_files if f in have)
    return [f"SQ2: protected confirmation files changed ({', '.join(hit)}); that needs a separate Fred or CEO ruling"] if hit else []

def record_shape_error(v):
    """R18/R19: None (nothing confirmed) is fine; anything else must pass gates.record_error, the one record validator."""
    from .gates import record_error
    return None if v is None else record_error(v)

def schema_errors(head):
    """R17 SQ-C: type check at the head on every PR."""
    from .gates import REQUIRED_FLAG
    bad = []
    for f, top, keys in ((TOS, "providers", {REQUIRED_FLAG: "bool"}), (CFG, "free_tier_guard", {"live_run_allowed": "bool"})):
        block = (head.get(f) or {}).get(top) if isinstance(head.get(f), dict) else None
        if not isinstance(block, dict): continue
        for pid, e in block.items():
            if not isinstance(e, dict): continue
            for k in keys:
                if k in e and type(e[k]) is not bool: bad.append(f"{f}: {top}.{pid}.{k} must be bool, got {type(e[k]).__name__}")
            if "vendor_confirmation" in e and (why := record_shape_error(e["vendor_confirmation"])):
                bad.append(f"{f}: {top}.{pid}.vendor_confirmation {why}")
    return [f"SQ-C (R17) schema: {b}" for b in bad]

def check(changed_files, base, head, base_has=None):
    lowered = gate_lowered(base, head) + protected_touched(changed_files, base_has) + schema_errors(head)
    confirmed = head_confirmed(head)
    flips, other = set(), []
    for f in sorted(DATA_FILES):
        for path, old, new in changed_paths(base.get(f), head.get(f)):
            pid = confirmation_pid(f, path)
            if pid is None: other.append((f, path, pid)); continue
            if not confirmed and old is MISSING and (new is None or new is False): continue   # bootstrap: new field at the closed default
            flips.add(pid); other.append((f, path, pid))
    if not flips: return lowered
    bad = sorted(f for f in changed_files if not flip_allowed_path(f))
    if len(flips) > 1: bad.append(f"more than one provider flipped ({', '.join(sorted(flips))})")
    bad += [f"{f}: {'.'.join(p)}" for f, p, pid in other if pid is None]
    return ([f"confirmation flip is not flip-only (R13): {'; '.join(bad)}. Record the vendor reply alone (that provider's confirmation fields, the evidence file, summary.md/decisions.md); everything else goes in its own PR"] if bad else []) + lowered

def parse(text):
    """Parse a committed yaml file (JSON-compatible flow YAML; PyYAML if installed). None when absent or unparsable."""
    if text is None: return None
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError: pass
    except Exception: return None
    try: return json.loads("\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#")))
    except ValueError: return None

def main(argv=None):
    args = list(argv if argv is not None else sys.argv[1:]) or ["origin/main"]
    base, rev = args[0], (args[1] if len(args) > 1 else "HEAD")
    root = Path(os.environ["AWI_REPO_ROOT"]) if os.environ.get("AWI_REPO_ROOT") else Path(__file__).resolve().parents[2]
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, check=True).stdout
    mb = git("merge-base", base, rev).strip()
    def show(rev, f):
        r = subprocess.run(["git", "-C", str(root), "show", f"{rev}:{f}"], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None
    base_has = [f for f in PROTECTED if subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{mb}:{f}"], capture_output=True).returncode == 0]
    probs = check([f for f in git("diff", "--name-only", "--no-renames", "-z", f"{mb}..{rev}").split("\0") if f],   # NUL-split: paths may hold spaces; --no-renames: a move lists the old path too
                  {f: parse(show(mb, f)) or {} for f in DATA_FILES}, {f: parse(show(rev, f)) for f in DATA_FILES}, base_has)
    for p in probs: print("FAIL:", p)
    return 1 if probs else 0

if __name__ == "__main__": sys.exit(main())
