import hashlib, json, re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

BENCH = Path(__file__).resolve().parent.parent          # site/bench
SITE = BENCH.parent                                      # site/
PUBLISHED_DIRS = [SITE / "data", SITE / "dist", BENCH / "runs"]   # mock output must never land here

def load_yaml(path):
    """YAML files here are written in JSON-compatible YAML (flow style) so they parse
    without PyYAML. Leading '#' comment lines are allowed. Uses PyYAML if installed."""
    text = Path(path).read_text()
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        return json.loads("\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#")))

def read_jsonl(path):
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]

def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def sha256_text(s):
    return hashlib.sha256(s.encode()).hexdigest()

def norm_url(u):
    s = urlsplit(u.strip())
    host = s.netloc.lower().removeprefix("www.")
    path = s.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, s.query, ""))

def domain(u):
    return urlsplit(u).netloc.lower().split(":")[0].removeprefix("www.")

def norm_text(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.casefold())).strip()

def is_under(path, parents):
    p = Path(path).resolve()
    return any(p == q.resolve() or q.resolve() in p.parents for q in parents)

def not_captured(v):
    return v is None or v == "NOT CAPTURED"
