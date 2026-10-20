import hashlib, json, re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

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

HOST_ALIASES = {"bbc.com": "bbc.co.uk"}
TRACKING_PARAM = re.compile(r"utm_.*|at_medium|at_campaign|srsltid", re.I)
REDIRECTORS = {("google.com", "/goto"): "url", ("google.com", "/url"): "q"}

def canon_host(netloc):
    h = netloc.lower().split("@")[-1].split(":")[0].removeprefix("www."); return HOST_ALIASES.get(h, h)

def norm_url(u):
    s = urlsplit(u.strip())
    rp = REDIRECTORS.get((canon_host(s.netloc), s.path))
    if rp:   # Google redirect: unwrap to the target URL; an opaque token (not a URL) cannot be resolved offline, so it is dropped
        t = dict(parse_qsl(s.query)).get(rp, "")
        if urlsplit(t).scheme in ("http", "https") and urlsplit(t).netloc: return norm_url(t)
        return urlunsplit(("https", canon_host(s.netloc), s.path, "", ""))
    path = s.path.rstrip("/") or "/"
    q = urlencode([(k, v) for k, v in parse_qsl(s.query, keep_blank_values=True) if not TRACKING_PARAM.fullmatch(k)])
    return urlunsplit(("https", canon_host(s.netloc), path, q, ""))

def domain(u):
    return canon_host(urlsplit(u).netloc)

def norm_text(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.casefold())).strip()

def is_under(path, parents):
    p = Path(path).resolve()
    return any(p == q.resolve() or q.resolve() in p.parents for q in parents)

def not_captured(v):
    return v is None or v == "NOT CAPTURED"
