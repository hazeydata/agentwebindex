#!/usr/bin/env python3
"""One-off: move full page text out of data/prices/<id>.json into data/cache/prices/<id>.txt. No network."""
import hashlib, json
from pathlib import Path
R=Path(__file__).resolve().parent.parent; C=R/"data/cache/prices"; C.mkdir(parents=True,exist_ok=True)
for f in sorted((R/"data/prices").glob("*.json")):
    d=json.loads(f.read_text())
    if "text" not in d: continue
    t="\n".join(d.pop("text")); (C/f"{f.stem}.txt").write_text(t)
    assert hashlib.sha256(t.encode()).hexdigest()==d["text_sha256"], f
    f.write_text(json.dumps(d,indent=1)); print("migrated",f.stem,"sha ok")
