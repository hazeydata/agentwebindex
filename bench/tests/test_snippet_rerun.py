"""Snippet re-run (Exa + Tavily, snippet_contains only): harness flag, stop check, workflow file. Mocks only; no network, no repo writes."""
import json, sys
from pathlib import Path
import pytest
BENCH = Path(__file__).resolve().parent.parent; SITE = BENCH.parent
sys.path.insert(0, str(BENCH)); sys.path.insert(0, str(SITE / "collectors")); sys.path.insert(0, str(BENCH / "tools"))
from awibench import runner
import benchmark as B
import snippet_rerun_check as C

ARGS = ["--mode", "full", "--only-providers", "exa,tavily", "--only-sets", "search_evergreen", "--only-check-types", "snippet_contains"]

class Stop(Exception): pass

def _capture(monkeypatch, argv):
    """Run main() up to runner.run (mocked to capture its kwargs and abort), so nothing is called or written."""
    got = {}
    def fake_run(**kw): got.update(kw); raise Stop
    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(B, "keyed_providers", lambda *a, **k: True)
    monkeypatch.setattr(runner, "provider_problems", lambda *a, **k: [])
    monkeypatch.setattr(sys, "argv", ["benchmark.py"] + argv)
    monkeypatch.setenv("EXA_API_KEY", "fake"); monkeypatch.setenv("TAVILY_API_KEY", "fake")
    with pytest.raises(Stop): B.main()
    return got

def test_mocked_dry_run(monkeypatch, capsys):
    got = _capture(monkeypatch, ARGS)
    out = capsys.readouterr().out
    assert "only-check-types: search_evergreen n=43 of 100 (snippet_contains)" in out
    sets = got["sets"]; assert list(sets) == ["search_evergreen"]
    s = sets["search_evergreen"]; assert len(s["rows"]) == 43 and s["sample_of"] == 100
    assert all(r["check"]["type"] == "snippet_contains" for r in s["rows"])
    assert [p["id"] for p in got["providers"]] == ["exa", "tavily"] and got["limits"] is not None
    lim = (B.load_yaml(B.BENCH / "config.yaml").get("run_schedule") or {}).get("full_limits", {})
    for lm in (got["limits"], {k: v for k, v in lim.items()}, {"search": 45}):
        assert len(runner.limit_sets(sets, lm)["search_evergreen"]["rows"]) == 43
    print("DRY-RUN n =", len(s["rows"]))

def test_flag_errors(monkeypatch, capsys):
    monkeypatch.setattr(runner, "run", lambda **k: pytest.fail("no run"))
    for argv in (["--mode", "full", "--only-check-types", "snippet_contains"], ARGS + ["--reprobe-offset", "6"], ARGS + ["--rebuild-only", "2026-01-01"]):
        monkeypatch.setattr(sys, "argv", ["benchmark.py"] + argv); assert B.main() == 2

def test_flag_absent_unchanged(monkeypatch, capsys):
    got = _capture(monkeypatch, ARGS[:6])
    s = got["sets"]["search_evergreen"]; assert len(s["rows"]) == 100 and "sample_of" not in s
    assert "only-check-types" not in capsys.readouterr().out

# ---- stop check
def _fixture(tmp_path, statuses=None, n=43, bad_line=None):
    d = tmp_path / "runs" / "2026-11-23"
    for pid in ("exa", "tavily"):
        (d / pid).mkdir(parents=True)
        lines = [{"status": "pass" if i % 2 else "fail", "http_status": 200} for i in range(n)]
        if bad_line and pid == "exa": lines[0]["http_status"] = bad_line
        (d / pid / "search_evergreen.jsonl").write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    st = statuses or {}
    (d / "_run-offset0.json").write_text(json.dumps({"providers": [{"provider": p, "category": "search", "status": st.get(p, "ran"), "reason": "x"} for p in ("exa", "tavily")]}))
    return d

def test_check_passes(tmp_path):
    assert C.check(_fixture(tmp_path)) == [] and C.main(["c", "2026-11-23", str(tmp_path)]) == 0

@pytest.mark.parametrize("kw", [{"n": 42}, {"bad_line": 402}, {"bad_line": 429}, {"statuses": {"exa": "stale_quota"}},
                                {"statuses": {"tavily": "stopped_paid_usage"}}, {"statuses": {"exa": "skipped_cap"}}])
def test_check_fails(tmp_path, kw, capsys):
    _fixture(tmp_path, **kw); assert C.main(["c", "2026-11-23", str(tmp_path)]) == 1
    assert "STOP:" in capsys.readouterr().out

# ---- workflow
def test_workflow_yaml():
    yaml = pytest.importorskip("yaml")
    txt = (SITE / ".github/workflows/snippet-rerun.yml").read_text(); wf = yaml.safe_load(txt)
    assert wf["name"] == "snippet-rerun"
    trig = wf.get(True, wf.get("on")); assert (list(trig) if isinstance(trig, dict) else [trig] if isinstance(trig, str) else trig) == ["workflow_dispatch"]
    assert wf["permissions"] == {"contents": "read"} and wf["concurrency"] == {"group": "benchmark", "cancel-in-progress": False}
    job = wf["jobs"]["rerun"]; assert job["permissions"] == {"contents": "read"} and job["env"] == {"MONTHLY_CAP_USD": "0"} and list(wf["jobs"]) == ["rerun"]
    assert "contents: write" not in txt.replace("{contents: write", "contents: write") and "write" not in json.dumps(wf["permissions"]) and "set -x" not in txt
    import re
    assert sorted(set(re.findall(r"secrets\.(\w+)", txt))) == ["EXA_API_KEY", "TAVILY_API_KEY"]
    steps = job["steps"]; withsec = [s for s in steps if "secrets." in json.dumps(s)]
    assert len(withsec) == 1 and set(withsec[0]["env"]) == {"EXA_API_KEY", "TAVILY_API_KEY"} and "secrets." not in withsec[0]["run"]
    run = withsec[0]["run"]; assert "--only-check-types snippet_contains" in run and "--only-providers exa,tavily" in run and "set -euo pipefail" in run
    assert not any("git push" in s.get("run", "") for s in steps)
    up = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact@v4")]
    assert len(up) == 1 and up[0]["if"] == "always()" and up[0]["with"]["name"] == "snippet-rerun-logs" and up[0]["with"]["retention-days"] == 14
    assert steps[0]["with"] == {"persist-credentials": False}
