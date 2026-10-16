"""AC10: no API keys in the repo. Scans tracked-candidate files (everything except dist/, data/cache/, .git/).
Exit 1 on any hit. Env var NAMES (e.g. EXA_API_KEY) are fine; values are not."""
import re, sys
from pathlib import Path
from .util import SITE

PATTERNS = [
    ("generic sk- key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Bearer token", re.compile(r"Bearer\s+[A-Za-z0-9._-]{24,}")),
    ("key assignment", re.compile(r"""(?i)\b[A-Z0-9_]*(?:API_KEY|API_TOKEN|SECRET|PASSWORD)\b\s*[:=]\s*["']?(?!\$\{\{)(?!os\.environ)[A-Za-z0-9_\-]{16,}""")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]
SKIP = {"dist", ".git", "cache", "__pycache__", "out"}

def scan(root):
    hits = []
    for f in Path(root).rglob("*"):
        if not f.is_file() or any(part in SKIP for part in f.relative_to(root).parts): continue
        try: text = f.read_text()
        except (UnicodeDecodeError, OSError): continue
        for n, line in enumerate(text.splitlines(), 1):
            for name, rx in PATTERNS:
                if rx.search(line): hits.append(f"{f.relative_to(root)}:{n}: {name}")
    return hits

def main(argv=None):
    root = Path(argv[0]) if argv else SITE
    hits = scan(root)
    for h in hits: print("SECRET?", h)
    print(f"{'FAIL' if hits else 'OK'}: {len(hits)} possible secrets under {root}"); return 1 if hits else 0

if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
