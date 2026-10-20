"""1b weekly news set generator (deterministic). AC3, AC13, D1.

python -m awibench.newsgen --date 2026-10-19T06:00:00-04:00 --seed 1 --fixtures DIR --out FILE
Live feed fetching is supported via --live but is NOT used in tests (fixtures only).
Same feeds + same date + same seed -> byte-identical output.

Query text (spec rule, final ruling 1): the cleaned headline, i.e. any trailing " - Outlet" / " | Outlet"
suffix removed, whitespace collapsed, trailing punctuation dropped, at most 14 words. No LLM rewrite.
"""
import argparse, datetime as dt, email.utils, json, math, random, re, sys, xml.etree.ElementTree as ET
from pathlib import Path
from .util import BENCH, load_yaml, domain, norm_url

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

FEED_ROOT = re.compile(r"<(rss|feed|rdf:RDF)\b")
UA = "AgentWebIndexBot/1.0"

def is_feed_xml(txt):
    """True when the document's root element is RSS (<rss>/<rdf:RDF>) or Atom (<feed>)."""
    return bool(FEED_ROOT.search(re.sub(r"<!--.*?-->|<\?.*?\?>|<!DOCTYPE[^>]*>", "", txt[:5000], flags=re.S).lstrip()[:200]))

def robots_allows(url, robots_txt, ua=UA):
    """RFC 9309 check: the group naming our product token (else '*'), longest matching rule wins, Allow wins ties.
    (urllib.robotparser drops a trailing '?' from rules, so 'Disallow: /r?' would wrongly block '/rss'.)"""
    import urllib.parse
    sp = urllib.parse.urlsplit(url); path = (sp.path or "/") + (f"?{sp.query}" if sp.query else "")
    token = ua.split("/")[0].lower(); groups = []; cur = None; last_ua = False
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line: continue
        k, v = (x.strip() for x in line.split(":", 1)); k = k.lower()
        if k == "user-agent":
            if not last_ua: cur = {"agents": [], "rules": []}; groups.append(cur)
            cur["agents"].append(v.lower()); last_ua = True
        elif k in ("allow", "disallow") and cur is not None:
            last_ua = False
            if v: cur["rules"].append((k == "allow", v))
        else: last_ua = False
    pick = [g for g in groups if token in g["agents"]] or [g for g in groups if "*" in g["agents"]]
    rules = [r for g in pick for r in g["rules"]]
    def match(rule, p):
        pat = re.escape(rule).replace(r"\*", ".*"); pat = pat[:-2] + "$" if pat.endswith(r"\$") else pat
        return re.match(pat, p) is not None
    best = None
    for allow, rule in rules:
        if match(rule, path) and (best is None or len(rule) > len(best[1]) or (len(rule) == len(best[1]) and allow)): best = (allow, rule)
    return True if best is None else best[0]

def live_read(f, _cache={}):
    """Fetch a feed only if the host's robots.txt allows it for our UA (raises otherwise; logged as feed_error)."""
    import urllib.request, urllib.parse, urllib.error
    get = lambda u: urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30).read().decode("utf-8", "replace")
    sp = urllib.parse.urlsplit(f["url"]); base = f"{sp.scheme}://{sp.netloc}"
    if base not in _cache:
        try: _cache[base] = get(base + "/robots.txt")
        except urllib.error.HTTPError as e: _cache[base] = "" if 400 <= e.code < 500 and e.code not in (401, 403) else None
        except Exception: _cache[base] = None
    if _cache[base] is None: raise PermissionError(f"robots.txt for {base} unreadable; feed not fetched")
    if not robots_allows(f["url"], _cache[base]): raise PermissionError(f"robots.txt disallows {f['url']}")
    return get(f["url"])

def generate(cfg, run_at, seed, read_feed, log):
    nc = cfg["news"]; items = []
    for feed in cfg["news_feeds"]:
        if not feed.get("url"):
            log.append({"event": "feed_dropped", "feed": feed["id"], "reason": feed.get("drop_reason", "no official feed URL configured")})
            continue
        got = []
        for page, url in enumerate([feed["url"]] + list(feed.get("more_urls") or [])):   # more_urls: older pages of the same feed
            try:
                txt = read_feed(feed if page == 0 else feed | {"url": url, "page": page})
                if txt is None: continue          # fixture reader: no file for this page
                if not is_feed_xml(txt):          # CEO ruling 2026-10-19: official RSS/Atom feeds only
                    log.append({"event": "feed_skipped", "feed": feed["id"], "page": page, "reason": "not an RSS/Atom document"}); continue
                got += parse_feed(txt, feed)
            except Exception as e:
                log.append({"event": "feed_error", "feed": feed["id"], "page": page, "error": f"{type(e).__name__}: {e}"})
        log.append({"event": "feed_read", "feed": feed["id"], "items": len(got)}); items += got
    lo, hi = run_at - dt.timedelta(hours=nc["max_age_h"]), run_at - dt.timedelta(hours=nc["min_age_h"])
    seen, pool = set(), []
    for x in sorted(items, key=lambda x: (x["published_at"], x["link"], x["feed"])):
        key = norm_url(x["link"]) if x["link"] else None   # same story in two section feeds (or with tracking params) counts once
        if not (lo <= x["published_at"] <= hi) or not x["link"] or key in seen or not x["title"]: continue
        seen.add(key); pool.append(x)
    random.Random(f"{seed}|{run_at.isoformat()}").shuffle(pool)
    caps = {"feed": nc["per_feed"]}   # final ruling (3): 15 per feed; from week domain_share_from_week also max_domain_share per source domain
    for x in pool: x["outlet"] = domain(x["link"])
    def pick(dom_cap):
        used = {k: {} for k in caps}; per_dom = {}; out = []
        for x in pool:
            if any(used[k].get(x[k], 0) >= c for k, c in caps.items()) or per_dom.get(x["outlet"], 0) >= dom_cap: continue
            for k in caps: used[k][x[k]] = used[k].get(x[k], 0) + 1
            per_dom[x["outlet"]] = per_dom.get(x["outlet"], 0) + 1; out.append(x)
            if len(out) == nc["total"]: break
        return out
    iy, iw, _ = run_at.isocalendar(); share = nc.get("max_domain_share")
    if share and f"{iy}-{iw:02d}" >= nc.get("domain_share_from_week", "0"):
        lim = math.floor(share * nc["total"])
        while True:
            chosen = pick(lim); m = math.floor(share * len(chosen))
            if all(sum(c["outlet"] == d for c in chosen) <= m for d in {c["outlet"] for c in chosen}): break
            lim = m
    else: chosen = pick(float("inf"))
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
    if n < nc["min_total"]: f.append(f"below minimum (n={n})")
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
    if a.fixtures:
        def read(f):
            p = Path(a.fixtures) / (f"{f['id']}.xml" if not f.get("page") else f"{f['id']}-p{f['page']}.xml")
            return p.read_text() if p.exists() else None
    elif a.live:
        read = live_read
    else: ap.error("need --fixtures DIR or --live")
    log = []; rows, name = generate(cfg, run_at, a.seed, read, log)
    out = a.out or str(BENCH / "sets" / name); write(rows, out, meta(rows, name, cfg, run_at, a.seed, log))
    for l in log: print(json.dumps(l))
    print("wrote", out); return 0

if __name__ == "__main__": sys.exit(main())
