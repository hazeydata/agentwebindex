"""AC1 + AC4 validator. Usage: python -m awibench.validate [SETS_DIR] [--config CONFIG]
Exits non-zero on any count, quota, schema, robots or allowlist error."""
import argparse, re, sys
from collections import Counter
from pathlib import Path
from .util import BENCH, load_yaml, read_jsonl, domain, norm_text, sha256_file

# SPEC-bench 1a, Spec final ruling Oct 15: no product category; 100 total.
EVERGREEN_N = 100
EVERGREEN_Q = {"facts/reference": 30, "docs/API lookup": 25, "local/entity": 10,
               "academic": 10, "long-tail": 15, "non-English": 10}
SCRAPE_N = 150
SCRAPE_Q = {"static": 50, "js_heavy": 40, "paywall_lite": 30, "anti_bot": 30}
HOST_CAP_PER_CLASS = 5
PAYWALL_LITE_MIN_CHARS = 5000
TASKS_N = 20
TASKS_Q = {"forms": 5, "multi_page_navigation": 5, "search_and_extract": 5, "js_widgets": 3, "file_download": 2}
SEARCH_CHECKS = {"url_domain", "url_exact", "snippet_contains"}
MARKERS = {"text_contains", "selector_text"}
PASS_TYPES = {"final_url_matches", "extracted_value_equals", "file_sha256", "dom_text_contains"}
URL = re.compile(r"^https?://[^\s/]+\.[^\s]+$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")

def legacy_flag(f):
    """Sets frozen before the 2026-10-15 owner ruling carry 'below minimum (n=X < 25)'; same meaning as 'below minimum (n=X)'."""
    import re; return re.sub(r"^below minimum \(n=(\d+) < \d+\)$", r"below minimum (n=\1)", f)

def short_week_flag(n): return f"short week (n={n})"

def _keys(row, need, where, errs):
    missing = [k for k in need if k not in row]
    extra = [k for k in row if k not in need]
    if missing: errs.append(f"{where}: missing keys {missing}")
    if extra: errs.append(f"{where}: unexpected keys {extra}")
    return not missing

def _quota(counter, quota, n_expected, n_actual, name, errs):
    if n_actual != n_expected: errs.append(f"{name}: {n_actual} rows, expected exactly {n_expected}")
    for k, v in quota.items():
        if counter.get(k, 0) != v: errs.append(f"{name}: category '{k}' has {counter.get(k, 0)}, expected {v}")
    for k in counter:
        if k not in quota: errs.append(f"{name}: unknown category '{k}'")

def _ids(rows, name, errs):
    c = Counter(r.get("id") for r in rows)
    for i, n in c.items():
        if n > 1: errs.append(f"{name}: duplicate id {i}")

def check_evergreen(rows, errs, warns):
    _ids(rows, "search_evergreen", errs)
    for i, r in enumerate(rows, 1):
        w = f"search_evergreen L{i}"
        if not _keys(r, ["id", "query", "category", "check", "source_url", "added"], w, errs): continue
        if not isinstance(r["query"], str) or not r["query"].strip(): errs.append(f"{w}: empty query")
        ch = r["check"]
        if not isinstance(ch, dict) or set(ch) != {"type", "value"} or ch.get("type") not in SEARCH_CHECKS:
            errs.append(f"{w}: bad check {ch}")
        elif isinstance(ch["value"], list):
            if ch["type"] != "url_exact" or not ch["value"] or not all(isinstance(u, str) and URL.match(u) for u in ch["value"]):
                errs.append(f"{w}: check.value list must be a non-empty list of public http(s) URLs (url_exact only)")
        elif not isinstance(ch["value"], str) or not ch["value"]: errs.append(f"{w}: empty check.value")
        if not URL.match(str(r["source_url"])): errs.append(f"{w}: source_url not a public http(s) URL")
        if not DATE.match(str(r["added"])): errs.append(f"{w}: added not a date")
        if re.search(r"\b(latest|current|newest|today|this year)\b", str(r["query"]), re.I):
            warns.append(f"{w}: query may not be evergreen (time-relative word)")
    _quota(Counter(r.get("category") for r in rows), EVERGREEN_Q, EVERGREEN_N, len(rows), "search_evergreen", errs)

def check_scrape(rows, errs, warns):
    _ids(rows, "scrape_urls", errs)
    for i, r in enumerate(rows, 1):
        w = f"scrape_urls L{i}"
        if not _keys(r, ["id", "url", "class", "marker", "min_chars", "robots_ok", "added"], w, errs): continue
        if not URL.match(str(r["url"])): errs.append(f"{w}: bad url")
        m = r["marker"]
        if not isinstance(m, dict) or set(m) != {"type", "value"} or m.get("type") not in MARKERS or not m.get("value"):
            errs.append(f"{w}: bad marker {m}")
        if not isinstance(r["min_chars"], int) or isinstance(r["min_chars"], bool) or r["min_chars"] < 1:
            errs.append(f"{w}: min_chars must be a positive int")
        if r["robots_ok"] is not True: errs.append(f"{w}: robots_ok must be true (AC4)")
        if not DATE.match(str(r["added"])): errs.append(f"{w}: added not a date")
    _quota(Counter(r.get("class") for r in rows), SCRAPE_Q, SCRAPE_N, len(rows), "scrape_urls", errs)
    hosts = Counter((r.get("class"), domain(str(r.get("url", "")))) for r in rows)
    for (cls, host), n in sorted(hosts.items(), key=str):
        if n > HOST_CAP_PER_CLASS: errs.append(f"scrape_urls: {n} {cls} rows on host {host}, max {HOST_CAP_PER_CLASS} per host per class (rule 8)")
    for i, r in enumerate(rows, 1):
        if r.get("class") == "paywall_lite" and isinstance(r.get("min_chars"), int) and r["min_chars"] < PAYWALL_LITE_MIN_CHARS:
            errs.append(f"scrape_urls L{i}: paywall_lite min_chars {r['min_chars']} < {PAYWALL_LITE_MIN_CHARS} (rule 9)")
    seen = {}
    for i, r in enumerate(rows, 1):
        m = r.get("marker")
        if not isinstance(m, dict) or not m.get("value"): continue
        k = norm_text(str(m["value"]))
        if k in seen: errs.append(f"scrape_urls L{i}: marker duplicates L{seen[k]} (rule 9: each marker unique to its page)")
        else: seen[k] = i

def path_blocked(url, path_rules):
    """Per-host robots path rules from config (task_site_path_rules). Returns a reason or None."""
    from urllib.parse import urlsplit
    r = path_rules.get(domain(url)); path = urlsplit(url).path or "/"
    if not r: return None
    if r.get("allow_prefix") and not any(path.startswith(a) for a in r["allow_prefix"]): return f"{domain(url)} path {path} outside allowed prefixes {r['allow_prefix']}"
    hit = next((d for d in r.get("disallow_prefix", []) if path.startswith(d)), None)
    return f"{domain(url)} path {path} blocked by robots.txt rule {hit}" if hit else None

def check_tasks(tasks, allowlist, errs, warns, path_rules=None):
    if not isinstance(tasks, list): errs.append("browser_tasks: top level must be a list"); return
    _ids(tasks, "browser_tasks", errs)
    allow = {d.lower() for d in allowlist}
    for i, t in enumerate(tasks, 1):
        w = f"browser_tasks #{i}"
        if not _keys(t, ["id", "start_url", "goal", "steps_hint", "pass", "timeout_s", "category"], w, errs): continue
        if not URL.match(str(t["start_url"])): errs.append(f"{w}: bad start_url")
        elif domain(t["start_url"]) not in allow:
            errs.append(f"{w}: start_url host {domain(t['start_url'])} not in demo-site allowlist (AC4)")
        else:
            why = path_blocked(t["start_url"], path_rules or {})
            if why: errs.append(f"{w}: {why} (AC4)")
        p = t["pass"]
        if not isinstance(p, dict) or set(p) != {"type", "value"} or p.get("type") not in PASS_TYPES:
            errs.append(f"{w}: bad pass {p}")
        if not isinstance(t["timeout_s"], int) or t["timeout_s"] <= 0: errs.append(f"{w}: timeout_s must be positive int")
    _quota(Counter(t.get("category") for t in tasks), TASKS_Q, TASKS_N, len(tasks), "browser_tasks", errs)

def check_news(rows, cfg, errs, warns, name="news"):
    caps = cfg["news"]
    if len(rows) > caps["total"]: errs.append(f"{name}: {len(rows)} rows > {caps['total']}")
    if len(rows) < caps["total"]: warns.append(f"{name}: {short_week_flag(len(rows))}" + (f", below minimum {caps['min_total']}" if len(rows) < caps["min_total"] else "") + " (published, flagged)")
    for key, cap in (("feed", caps["per_feed"]),):   # final ruling (3): per-feed is the only cap
        for k, n in Counter(r.get(key) for r in rows).items():
            if n > cap: errs.append(f"{name}: {key} '{k}' has {n} > cap {cap}")
    for i, r in enumerate(rows, 1):
        if r.get("check", {}).get("type") not in ("url_exact", "url_domain"): errs.append(f"{name} L{i}: bad check")
    m = re.match(r"news_(\d{4}-\d{2})", name); share = caps.get("max_domain_share")
    if share and m and m.group(1) >= caps["domain_share_from_week"] and rows:
        for d, n in Counter(domain(str(r.get("source_url", ""))) for r in rows).items():
            if n > share * len(rows): errs.append(f"{name}: source domain {d} has {n}/{len(rows)} rows > {share:.0%} cap (from week {caps['domain_share_from_week']})")

def check_freeze(sd, name):
    mp = Path(sd) / name
    if not mp.exists(): return [f"missing freeze manifest {name}"]
    import json as _j
    sums = _j.loads(mp.read_text()).get("sha256") or {}
    if not sums: return [f"{name}: no sha256 entries"]
    errs = [f"{name}: missing {f}" for f in sums if not (Path(sd) / f).exists()]
    errs += [f"{f}: sha256 does not match {name}" for f, h in sums.items() if (Path(sd) / f).exists() and sha256_file(Path(sd) / f) != h]
    return errs + [f"{f}: not in {name}" for f in ("search_evergreen.jsonl", "scrape_urls.jsonl", "browser_tasks.yaml") if f not in sums]

def validate_by_set(sets_dir, cfg, only=None):
    """{set_name: (errors, warnings)} so one bad or missing set doesn't block the others. only: 'news' or None."""
    sd = Path(sets_dir); out = {}
    fixed = {"search_evergreen": ("search_evergreen.jsonl", lambda p, e, w: check_evergreen(read_jsonl(p), e, w)),
             "scrape_urls": ("scrape_urls.jsonl", lambda p, e, w: check_scrape(read_jsonl(p), e, w)),
             "browser_tasks": ("browser_tasks.yaml", lambda p, e, w: check_tasks(load_yaml(p), cfg["task_site_allowlist"], e, w, cfg.get("task_site_path_rules")))}
    if only != "news":
        for name, (fname, fn) in fixed.items():
            e, w = [], []
            if not (sd / fname).exists(): e.append(f"missing {fname}")
            else:
                try: fn(sd / fname, e, w)
                except ValueError as ex: e.append(f"{fname}: parse error: {ex}")
            out[name] = (e, w)
    for nf in sorted(sd.glob("news_*.jsonl")):
        e, w = [], []
        try:
            rows = read_jsonl(nf); check_news(rows, cfg, e, w, nf.name)
            mf = nf.with_suffix(".meta.json")
            if not mf.exists(): e.append(f"{nf.name}: missing {mf.name}")
            else:
                import json as _j; m = _j.loads(mf.read_text())
                if m.get("count") != len(rows): e.append(f"{mf.name}: count {m.get('count')} != {len(rows)} rows")
                from .newsgen import week_flags
                if bool(m.get("short_week")) != (len(rows) < cfg["news"]["total"]) or [legacy_flag(f) for f in (m.get("flags") or [])] != week_flags(len(rows), cfg["news"]):
                    e.append(f"{mf.name}: short_week flag inconsistent with count")
        except ValueError as ex: e.append(f"{nf.name}: parse error: {ex}")
        out[nf.stem] = (e, w)
    return out

def validate(sets_dir, cfg, only=None):
    errs, warns = [], []
    for e, w in validate_by_set(sets_dir, cfg, only).values(): errs += e; warns += w
    if only != "news" and cfg.get("freeze_manifest"): errs += check_freeze(sets_dir, cfg["freeze_manifest"])
    return errs, warns

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("sets_dir", nargs="?", default=str(BENCH / "sets"))
    ap.add_argument("--config", default=str(BENCH / "config.yaml")); ap.add_argument("--only", choices=["news"])
    a = ap.parse_args(argv)
    errs, warns = validate(a.sets_dir, load_yaml(a.config), a.only)
    for w in warns: print("WARN", w)
    for e in errs: print("ERROR", e)
    print(f"{'FAIL' if errs else 'OK'}: {len(errs)} errors, {len(warns)} warnings in {a.sets_dir}")
    return 1 if errs else 0

if __name__ == "__main__": sys.exit(main())
