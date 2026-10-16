"""§3 freshness metric. AC7.

probes: {query_id: [{offset_h, results:[{url, title, published_at}]}]} (offset 0 = the main run;
re-probes of misses at +6, +24, +72 h). Rows come from news_YYYY-WW.jsonl.
fresh_hit@10: share of queries whose offset-0 top 10 contains a SAME-EVENT article.
Same-event (final ruling 2): the same canonical URL, OR an article published within 72 h (inclusive) of the
expected article whose title has Jaccard similarity >= 0.5 with the headline on content tokens
(casefolded, punctuation stripped, stopwords removed).
median_lag_h: median over found queries of (first probe time that finds it - published_at).
Not found by +72 h = censored; censored share reported. Probe time = run_at + offset_h.
"""
import datetime as dt, statistics
from .util import norm_url, norm_text

REPROBE_OFFSETS_H = (6, 24, 72)

JACCARD_MIN = 0.5
WINDOW_H = 72
STOPWORDS = frozenset("""a an and are as at be been but by for from has have he her his i if in into is it its of on or our
she so than that the their them they this to was we were what when where which who will with you your after over says said new
""".split())

def content_tokens(text):
    return {t for t in norm_text(text or "").split() if t not in STOPWORDS}

def jaccard(a, b):
    A, B = content_tokens(a), content_tokens(b)
    return len(A & B) / len(A | B) if A | B else 0.0

def same_event(row, r, jaccard_min=JACCARD_MIN, window_h=WINDOW_H):
    if norm_url(r["url"]) == norm_url(row["check"]["value"]): return True
    if not r.get("published_at") or not r.get("title") or not row.get("headline"): return False
    dt_h = abs((dt.datetime.fromisoformat(r["published_at"]) - dt.datetime.fromisoformat(row["published_at"])).total_seconds()) / 3600
    return dt_h <= window_h and jaccard(row["headline"], r["title"]) >= jaccard_min

def _hit(row, results):
    return any(same_event(row, r) for r in results[:10])

def freshness(rows, probes, run_at, week, set_sha):
    n = len(rows); hits = 0; lags = []; censored = 0
    for row in rows:
        ps = sorted(probes.get(row["id"], []), key=lambda p: p["offset_h"])
        first = next((p for p in ps if p["offset_h"] <= 72 and _hit(row, p["results"])), None)
        if first and first["offset_h"] == 0: hits += 1
        if first is None: censored += 1; continue
        t = run_at + dt.timedelta(hours=first["offset_h"])
        lags.append((t - dt.datetime.fromisoformat(row["published_at"])).total_seconds() / 3600)
    return {"n": n, "week": week, "set_sha": set_sha,
            "fresh_hit_at_10": hits / n if n else None,
            "median_lag_h": statistics.median(lags) if lags else None,
            "censored_share": censored / n if n else None}

def misses_due(rows, probes, offset_h):
    """Query ids needing a re-probe at offset_h (not yet found at any earlier probe)."""
    out = []
    for row in rows:
        ps = [p for p in probes.get(row["id"], []) if p["offset_h"] < offset_h]
        if not any(_hit(row, p["results"]) for p in ps) and not any(p["offset_h"] == offset_h for p in probes.get(row["id"], [])):
            out.append(row["id"])
    return out
