"""Browser fetch task set derived from a frozen weekly news set (no new rows, no edits to the frozen file).

Each news row becomes one task: open the article's canonical URL (row.check.value) in the provider's browser and
pass if the rendered page text contains the feed headline (dom_text_contains). The set sha is the frozen news set's sha,
so every fetch log line traces back to the same frozen file the search providers ran on.
"""
from pathlib import Path
from .util import read_jsonl, sha256_file

def tasks(news_rows, timeout_s=30):
    return [{"id": r["id"], "start_url": r["check"]["value"], "goal": "load the article and return its rendered text",
             "pass": {"type": "dom_text_contains", "value": r["headline"]}, "timeout_s": timeout_s, "category": "news_fetch"}
            for r in news_rows if (r.get("check") or {}).get("type") == "url_exact"]

def from_news_file(path, timeout_s=30):
    """Returns (set_name, {"rows": [...], "sha": news_sha}). news_2026-42.jsonl -> newsfetch_2026-42."""
    p = Path(path); rows = read_jsonl(p)
    return "newsfetch_" + p.stem.removeprefix("news_"), {"rows": tasks(rows, timeout_s), "sha": sha256_file(p)}
