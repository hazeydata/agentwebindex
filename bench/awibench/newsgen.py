"""1b weekly news set generator (deterministic). AC3, AC13, D1.

python -m awibench.newsgen --date 2026-10-19T06:00:00-04:00 --seed 1 --fixtures DIR --out FILE
Live feed fetching is supported via --live but is NOT used in tests (fixtures only).
Same feeds + same date + same seed -> byte-identical output.

Query text (spec rule, final ruling 1): the cleaned headline, i.e. any trailing " - Outlet" / " | Outlet"
suffix removed, whitespace collapsed, trailing punctuation dropped, at most 14 words. No LLM rewrite.
"""
import argparse, datetime as dt, email.utils, json, random, re, sys, xml.etree.ElementTree as ET
from pathlib import Path
from .util import BENCH, load_yaml, domain

def parse_feed(xml_text, feed):
    root = ET.fromstring(xml_text); out = []
    atom = "{http://www.w3.org/2005/Atom}"
    items = root.iter("item") if root.find(".//item") is not None else root.iter(atom + "entry")
    for it in items:
        g = lambda t: (it.findtext(t) or it.findtext(atom + t) or "").strip()
        link = g("link")
        if not link:
            le = it.find(atom + "link"); link = le.get("href", "") if le is not None else ""
        when = g("pubDate") or g("published") or g("updated")
        try:
            ts = email.utils.parsedate_to_datetime(when) if "," in when or when[:3].isalpha() else dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
        except Exception:
            continue
        if ts.tzinfo is None: continue          # can't place undated-zone items in the 24-72h window
        out.append({"title": " ".join(g("title").split()), "link": link, "published_at": ts.astimezone(dt.timezone.utc),
                    "section": g("category") or feed["section"], "feed": feed["id"]})
    return out

def make_query(title, max_words):
    t = re.sub(r"\s+[-|–—]\s+[^-|–—]{2,40}$", "", title).strip()
    t = re.sub(r"[\s.,;:!?]+$", "", t)
    return " ".join(t.split()[:max_words])

def generate(cfg, run_at, seed, read_feed, log):
    nc = cfg["news"]; items = []
    for feed in cfg["news_feeds"]:
        if not feed.get("url"):
            log.append({"event": "feed_dropped", "feed": feed["id"], "reason": feed.get("drop_reason", "no official feed URL configured")})
            continue
        try:
            got = parse_feed(read_feed(feed), feed)
        except Exception as e:
            log.append({"event": "feed_error", "feed": feed["id"], "error": f"{type(e).__name__}: {e}"}); continue
        log.append({"event": "feed_read", "feed": feed["id"], "items": len(got)}); items += got
    lo, hi = run_at - dt.timedelta(hours=nc["max_age_h"]), run_at - dt.timedelta(hours=nc["min_age_h"])
    seen, pool = set(), []
    for x in sorted(items, key=lambda x: (x["published_at"], x["link"], x["feed"])):
        if not (lo <= x["published_at"] <= hi) or not x["link"] or x["link"] in seen or not x["title"]: continue
        seen.add(x["link"]); pool.append(x)
    random.Random(f"{seed}|{run_at.isoformat()}").shuffle(pool)
    caps = {"feed": nc["per_feed"]}   # final ruling (3): 15 per feed is the only cap (no outlet or section caps)
    used = {k: {} for k in caps}; chosen = []
    for x in pool:
        x["outlet"] = domain(x["link"])
        if any(used[k].get(x[k], 0) >= c for k, c in caps.items()): continue
        for k in caps: used[k][x[k]] = used[k].get(x[k], 0) + 1
        chosen.append(x)
        if len(chosen) == nc["total"]: break
    iso_y, iso_w, _ = run_at.isocalendar()
    rows = []
    for n, x in enumerate(chosen, 1):
        rows.append({"id": f"news-{iso_y}-W{iso_w:02d}-{n:03d}", "query": make_query(x["title"], nc["query_max_words"]),
                     "headline": x["title"], "check": {"type": "url_exact", "value": x["link"]},
                     "alt_check": {"type": "url_domain", "value": x["outlet"], "within_h": 72},
                     "published_at": x["published_at"].isoformat(), "section": x["section"], "outlet": x["outlet"],
                     "feed": x["feed"], "source_url": x["link"], "added": run_at.date().isoformat()})
    log.append({"event": "generated", "rows": len(rows), "pool": len(pool), "target": nc["total"], "min": nc["min_total"],
                "below_min": len(rows) < nc["min_total"]})
    return rows, f"news_{iso_y}-{iso_w:02d}.jsonl"

def week_flags(n, nc):
    """SPEC-bench AC3 (updated 21:12): every week publishes. Under 50 -> 'short week (n=X)'; under 25 also marked below
    the minimum; n=0 publishes as 'no data (n=0)' with no scores."""
    if n == 0: return ["no data (n=0)"]
    f = []
    if n < nc["total"]: f.append(f"short week (n={n})")
    if n < nc["min_total"]: f.append(f"below minimum (n={n} < {nc['min_total']})")
    return f

def meta(rows, name, cfg, run_at, seed, log):
    nc = cfg["news"]
    return {"set": name[:-6], "count": len(rows), "target_max": nc["total"], "min": nc["min_total"], "meets_min": len(rows) >= nc["min_total"],
            "short_week": len(rows) < nc["total"], "flags": week_flags(len(rows), nc),
            "run_at": run_at.isoformat(), "seed": str(seed), "caps": {"per_feed": nc["per_feed"]},
            "window_h": [nc["min_age_h"], nc["max_age_h"]], "per_feed_counts": {f: sum(r["feed"] == f for r in rows) for f in sorted({r["feed"] for r in rows})},
            "feed_log": log}

def write(rows, path, meta_doc=None):
    p = Path(path)
    if p.exists(): raise FileExistsError(f"{p} is frozen and may not be overwritten")
    p.write_text("".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows))
    if meta_doc is not None:
        from .util import sha256_file
        p.with_suffix(".meta.json").write_text(json.dumps(meta_doc | {"sha256": sha256_file(p)}, indent=1, sort_keys=True) + "\n")

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--date", required=True); ap.add_argument("--seed", default="0")
    ap.add_argument("--config", default=str(BENCH / "config.yaml")); ap.add_argument("--fixtures")
    ap.add_argument("--live", action="store_true"); ap.add_argument("--out"); a = ap.parse_args(argv)
    cfg = load_yaml(a.config); run_at = dt.datetime.fromisoformat(a.date)
    if a.fixtures: read = lambda f: (Path(a.fixtures) / f"{f['id']}.xml").read_text()
    elif a.live:
        import urllib.request
        read = lambda f: urllib.request.urlopen(urllib.request.Request(f["url"], headers={"User-Agent": "AgentWebIndexBot/1.0"}), timeout=30).read().decode("utf-8", "replace")
    else: ap.error("need --fixtures DIR or --live")
    log = []; rows, name = generate(cfg, run_at, a.seed, read, log)
    out = a.out or str(BENCH / "sets" / name); write(rows, out, meta(rows, name, cfg, run_at, a.seed, log))
    for l in log: print(json.dumps(l))
    print("wrote", out); return 0

if __name__ == "__main__": sys.exit(main())
