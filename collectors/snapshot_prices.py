#!/usr/bin/env python3
"""Daily pricing-page snapshotter (stdlib only).

For each provider: fetch the public pricing page ONCE, extract visible text and
every $-amount found in it. Git-tracked: data/prices/<id>.json (prices + sha256 of
page text) and data/prices/history/<id>.jsonl. Full page text goes ONLY to the
gitignored data/cache/prices/<id>.txt; a text diff is produced when the previous
cache file exists (locally, or restored from the previous run's artifact).
Fetch failures are recorded honestly (status "error"), never filled in.
Nothing here invents a price: values are only what regexes find on the page.
"""
import argparse, datetime as dt, difflib, hashlib, html, json, re, sys, urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT / "bench"))
from awibench import gates   # noqa: E402  R10 shared vendor-confirmation gate
from awibench.util import load_yaml   # noqa: E402
PRICES = ROOT / "data" / "prices"
CACHE = ROOT / "data" / "cache" / "prices"   # gitignored; uploaded as an Actions artifact
UA = "AgentWebIndexBot/1.0 (+price-history; one fetch per day)"
MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:/|per)\s?[A-Za-z0-9 ]{1,20})?")

class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self,t,a):
        if t in ("script","style","noscript","svg"): self.skip+=1
    def handle_endtag(self,t):
        if t in ("script","style","noscript","svg") and self.skip: self.skip-=1
    def handle_data(self,d):
        if not self.skip and d.strip(): self.parts.append(" ".join(d.split()))

def extract(raw):
    p=Text(); p.feed(raw); lines=p.parts
    prices=sorted(set(" ".join(m.group(0).split()) for m in MONEY.finditer("\n".join(lines))))
    return lines, prices

def fetch(url, timeout=30):
    req=urllib.request.Request(url, headers={"User-Agent":UA,"Accept":"text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(5_000_000).decode(r.headers.get_content_charset() or "utf-8","replace")

NO_PRICE_SNAPSHOT = ("keenable",)   # AWI-PROVIDER-KEENABLE-001 AC21/AC22

def snapshot_providers(provs, tos):
    """AWI-PROVIDER-CONTEXTDEV-001 R10: a provider still waiting for written vendor confirmation (gates.vendor_confirmed) is not
    fetched and gets no published data/prices file (Context.dev today)."""
    # AWI-PROVIDER-KEENABLE-001 AC21/AC22: a provider in NO_PRICE_SNAPSHOT (keenable) is never fetched here, confirmed or not (no keenable.ai page fetch, no Prices row)
    return [p for p in provs if gates.vendor_confirmed((tos or {}).get(p["id"])) and p["id"] not in NO_PRICE_SNAPSHOT]

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--only", nargs="*"); a=ap.parse_args()
    provs=snapshot_providers(json.loads((ROOT/"collectors/providers.json").read_text())["providers"], load_yaml(ROOT/"bench/tos_matrix.yaml")["providers"])
    (PRICES/"history").mkdir(parents=True, exist_ok=True); CACHE.mkdir(parents=True, exist_ok=True)
    now=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    summary=[]
    for p in provs:
        if a.only and p["id"] not in a.only: continue
        out=PRICES/f"{p['id']}.json"
        prev=json.loads(out.read_text()) if out.exists() else None
        snap={"provider":p["id"],"name":p["name"],"url":p["pricing_url"],"fetched_at":now}
        try:
            status,raw=fetch(p["pricing_url"])
            lines,prices=extract(raw)
            snap.update(status="ok",http_status=status,text_sha256=hashlib.sha256("\n".join(lines).encode()).hexdigest(),
                        text_lines=len(lines),prices_found=prices)
            if not prices: snap["note"]="Page fetched but no $ amounts visible in static HTML (likely JS-rendered)."
        except Exception as e:
            snap.update(status="error",error=f"{type(e).__name__}: {e}"[:300],prices_found=[])
        cf=CACHE/f"{p['id']}.txt"
        prev_text=cf.read_text().splitlines() if cf.exists() else None
        if snap["status"]=="ok": cf.write_text("\n".join(lines))
        diff={"previous_fetched_at":None,"changed":None}
        if prev and prev.get("status")=="ok" and snap["status"]=="ok":
            diff={"previous_fetched_at":prev["fetched_at"],"changed":prev["text_sha256"]!=snap["text_sha256"],
                  "prices_added":sorted(set(snap["prices_found"])-set(prev["prices_found"])),
                  "prices_removed":sorted(set(prev["prices_found"])-set(snap["prices_found"])),
                  "text_diff":(list(difflib.unified_diff(prev_text,lines,lineterm="",n=0))[:200] if prev_text is not None
                               else "previous page text not in cache; compare by sha256 only")}
        elif prev: diff={"previous_fetched_at":prev["fetched_at"],"changed":None,"note":"previous or current fetch failed"}
        snap["diff"]=diff
        out.write_text(json.dumps(snap,indent=1))
        with open(PRICES/"history"/f"{p['id']}.jsonl","a") as h:
            h.write(json.dumps({k:snap.get(k) for k in("fetched_at","status","http_status","error","text_sha256","prices_found")}|
                               {"changed":diff.get("changed"),"prices_added":diff.get("prices_added",[]),"prices_removed":diff.get("prices_removed",[])})+"\n")
        summary.append({"provider":p["id"],"status":snap["status"],"http":snap.get("http_status"),"n_prices":len(snap["prices_found"]),
                        "error":snap.get("error"),"changed":diff.get("changed")})
        print(json.dumps(summary[-1]))
    (PRICES/"index.json").write_text(json.dumps({"generated_at":now,"providers":summary},indent=1))
    return 0

if __name__=="__main__": sys.exit(main())
