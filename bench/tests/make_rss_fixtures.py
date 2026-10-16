"""Writes synthetic MOCK RSS fixtures into fixtures/rss/ (deterministic). All content is fake (example.* hosts)."""
import datetime as dt, email.utils
from pathlib import Path
OUT = Path(__file__).parent / "fixtures" / "rss"; OUT.mkdir(parents=True, exist_ok=True)
RUN = dt.datetime(2026, 10, 19, 10, 0, tzinfo=dt.timezone.utc)   # Mon 06:00 ET
def feed(fid, n, hosts, cats):
    items = []
    for i in range(n):
        ts = RUN - dt.timedelta(hours=6 + i * 2.5)          # spans 6h..~80h old: some outside 24-72h window
        host = hosts[i % len(hosts)]; cat = cats[i % len(cats)] if cats else None
        items.append(f"<item><title>MOCK {fid} story {i:02d}: Example Corp announces widget {i} - Mock Outlet</title>"
                     f"<link>https://{host}/{fid}/story-{i:02d}</link><pubDate>{email.utils.format_datetime(ts)}</pubDate>"
                     + (f"<category>{cat}</category>" if cat else "") + "</item>")
    items.append("<item><title>MOCK undated item</title><link>https://undated.example/x</link></item>")
    (OUT / f"{fid}.xml").write_text(f'<?xml version="1.0"?><rss version="2.0"><channel><title>MOCK {fid}</title>{"".join(items)}</channel></rss>\n')
feed("hn", 32, [f"site{k}.example" for k in range(12)], None)
feed("bbc_world", 30, ["bbc-mock.example", "bbc-mock2.example"], ["world", "europe", "asia"])
feed("npr", 30, ["npr-mock.example", "npr-mock2.example", "npr-mock3.example"], ["politics", "health", "science"])
feed("github_changelog", 20, ["github-mock.example"], ["changelog"])
print("wrote", sorted(p.name for p in OUT.glob("*.xml")))
