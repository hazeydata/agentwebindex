"""AC9 dry run: mock providers -> run logs -> tables, all under a temp dir. No network, no spend."""
import json, sys, tempfile, datetime as dt
from pathlib import Path
B = Path(__file__).resolve().parent.parent; sys.path[:0] = [str(B), str(Path(__file__).parent)]
import mocks
from awibench import runner, tables, newsgen, validate
from awibench.util import load_yaml, read_jsonl, sha256_file
td = tempfile.TemporaryDirectory(); tmp = Path(td.name); sd = tmp / "sets"; mocks.make_sets(sd)
cfg = mocks.mock_cfg(load_yaml(B / "config.yaml"))
log = []; rows, name = newsgen.generate(cfg, dt.datetime.fromisoformat("2026-10-19T06:00:00-04:00"), "0", lambda f: (Path(__file__).parent / "fixtures/rss" / f"{f['id']}.xml").read_text(), log)
newsgen.write(rows, sd / name, newsgen.meta(rows, name, cfg, dt.datetime.fromisoformat("2026-10-19T06:00:00-04:00"), "0", log)); print("news meta:", {k: v for k, v in json.loads((sd / name).with_suffix(".meta.json").read_text()).items() if k in ("count", "min", "meets_min", "per_feed_counts")}); print("validator errors:", validate.validate(sd, cfg)[0])
sets = {s: {"rows": read_jsonl(sd / f"{s}.jsonl"), "sha": sha256_file(sd / f"{s}.jsonl")} for s in ("search_evergreen", "scrape_urls", name[:-6])}
sets["browser_tasks"] = {"rows": load_yaml(sd / "browser_tasks.yaml"), "sha": sha256_file(sd / "browser_tasks.yaml")}
provs = [{"id": "mock_search", "categories": ["search"], "key_env": "K1"}, {"id": "mock_scrape", "categories": ["scrape"], "key_env": "K2"},
         {"id": "mock_browser", "categories": ["browser"], "key_env": "K3"}]
models = {"search": "credits", "scrape": "per_request", "browser": "per_hour_browser"}
pricing = {p["id"]: {c: mocks.pricing_fixture(models[c]) | {"free_tier": {"units_per_month": 5000, "needs_card": False, "note": "MOCK free tier"}} for c in p["categories"]} for p in provs}
pricing["mock_scrape"]["scrape"]["tiers"]["volume"]["unit_price"] = "NOT CAPTURED"
del pricing["mock_browser"]["browser"]["tiers"]["entry"]["rounding_minutes"]   # shows a lower-bound cell
ad = mocks.MockAdapters(); adapters = {}
for p in provs: adapters.update(ad.table(p["id"], p["categories"]))
s = runner.run(providers=provs, sets=sets, pricing=pricing, tos={"mock_scrape": {"allowed": "no", "checked_at": "MOCK", "terms_url": "https://mock.example/terms"}},
               adapters=adapters, cfg=cfg, runs_dir=tmp / "runs", ledger_path=tmp / "spend.json", run_date="2026-10-19", env={"K1": "x", "K2": "x", "K3": "x"}, mock=True,
               now_fn=lambda: dt.datetime.fromisoformat("2026-10-19T11:23:00+00:00"))
print("providers:", [(p["provider"], p["status"], p["calls"], p.get("quota_used")) for p in s["providers"]])
doc = tables.build(tmp / "runs", pricing, sd); print("untraced cells:", tables.untraced_cells(doc, tmp))
for cat, t in doc["tables"].items():
    for r in t:
        print(f"MOCK {cat:7} {r['set']:18} set_rows={r['set_rows']} n={r['attempted']['v']:>3} tos_skip={r['skipped_tos']['count']:>2} success={r['success_rate']['v']:.2f} "
              f"entry/1k={r['entry_tier_cost_per_1k']['display']} entry/succ1k={r['entry_tier_cost_per_successful_1k']['display']} "
              f"vol/1k={r['volume_tier_cost_per_1k']['display']} vol/succ1k={r['volume_tier_cost_per_successful_1k']['display']} | {r['free_quota_note']}")
for f in doc["freshness"]: print("MOCK freshness", f["provider"], f["week"], "n", f["n"], {k: f[k]["v"] for k in ("fresh_hit_at_10", "median_lag_h", "censored_share")})
print("news log:", [l for l in log if l["event"] != "feed_read"])
td.cleanup()
