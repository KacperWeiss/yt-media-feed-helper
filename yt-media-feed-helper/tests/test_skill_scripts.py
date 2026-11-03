"""Type (b) skill scripts: --help, working without MCP and network."""
import csv
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parent.parent / "skills"
SCRIPTS = {
    "validate": SKILLS / "taxonomy-edit/scripts/validate_catalog.py",
    "diag": SKILLS / "diag-stats/scripts/diag_stats.py",
    "reports": SKILLS / "reports-browse/scripts/reports_index.py",
    "export": SKILLS / "catalog-export/scripts/export_catalog.py",
}


def run(script, *args):
    return subprocess.run([sys.executable, str(SCRIPTS[script]), *map(str, args)], capture_output=True, timeout=60)


@pytest.mark.parametrize("name", list(SCRIPTS))
def test_help(name):
    cp = run(name, "--help")
    assert cp.returncode == 0 and b"usage" in cp.stdout.lower()


def test_validate_ok_on_fixture(ws):
    cp = run("validate", "--workspace", ws.root)
    assert cp.returncode == 0, cp.stdout.decode("utf-8")
    assert "0 errors" in cp.stdout.decode("utf-8")


def test_validate_fix_synonyms(ws):
    text = ws.channels_file.read_text(encoding="utf-8").replace("value: Hearts of Iron,", "value: HoI4,")
    ws.channels_file.write_text(text, encoding="utf-8")
    out = json.loads(run("validate", "--workspace", ws.root, "--json").stdout)
    assert any(i["code"] == "SYNONYM_USED" for i in out["issues"])
    fixed = json.loads(run("validate", "--workspace", ws.root, "--json", "--fix-synonyms").stdout)
    assert fixed["fixed_synonyms"] == 1 and not any(i["code"] == "SYNONYM_USED" for i in fixed["issues"])


def test_export_md_csv(ws):
    md = run("export", "--workspace", ws.root, "--filter", "content=Gaming", "--filter", "communication=Official,Media partner",
             "--columns", "title,communication,specialization").stdout.decode("utf-8")
    assert "Paradox Interactive" in md and "Strategy Partner" in md and "Polski Strateg" not in md
    out = run("export", "--workspace", ws.root, "--format", "csv", "--filter", "language_region=PL", "--out", "reports/catalog-export.csv")
    assert out.returncode == 0
    rows = list(csv.DictReader(io.StringIO((ws.reports / "catalog-export.csv").read_text(encoding="utf-8"))))
    assert {r["title"] for r in rows} == {"Polski Strateg", "Czytelnia Recenzji"}
    assert "Supporter*" in next(r for r in rows if r["title"] == "Polski Strateg")["subscription"]
    bad = run("export", "--workspace", ws.root, "--filter", "content=Chess")
    assert bad.returncode != 0 and "Chess" in bad.stderr.decode("utf-8")
    # Polish aliases still resolve (functional data kept in the taxonomy)
    pl = run("export", "--workspace", ws.root, "--filter", "communication=Official", "--columns", "title").stdout.decode("utf-8")
    assert "Paradox Interactive" in pl


def test_diag_stats(ws):
    logs = ws.logs
    recs = [
        {"ts": "2099-01-01T10:00:00.000Z", "event": "SubagentStart", "agent_id": "a1", "agent_type": "yt-media-feed-helper:scout", "session_id": "s"},
        {"ts": "2099-01-01T10:00:01.000Z", "event": "SubagentStart", "agent_id": "a2", "agent_type": "yt-media-feed-helper:scout", "session_id": "s"},
        {"ts": "2099-01-01T10:00:02.000Z", "event": "PostToolUse", "tool_short": "ytfeed.yt_comments", "duration_ms": 100, "result_bytes": 4000, "quota_cost": 1, "actor": "subagent", "agent_type": "yt-media-feed-helper:scout", "session_id": "s"},
        {"ts": "2099-01-01T10:00:03.000Z", "event": "PostToolUse", "tool_short": "ytfeed.yt_comments", "duration_ms": 300, "result_bytes": 4000, "quota_cost": 1, "actor": "subagent", "agent_type": "yt-media-feed-helper:scout", "session_id": "s"},
        {"ts": "2099-01-01T10:00:04.000Z", "event": "PostToolUseFailure", "tool_short": "Bash", "error": "x", "actor": "main", "session_id": "s"},
        {"ts": "2099-01-01T10:00:05.000Z", "event": "SubagentStop", "agent_id": "a1", "agent_type": "yt-media-feed-helper:scout", "session_id": "s"},
        {"ts": "2099-01-01T10:00:06.000Z", "event": "SubagentStop", "agent_id": "a2", "agent_type": "yt-media-feed-helper:scout", "session_id": "s"},
    ]
    (logs / "diag-2099-01-01.jsonl").write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
    (logs / "guard-2099-01-01.jsonl").write_text(json.dumps({"ts": "2099-01-01T10:00:00.000Z", "decision": "deny", "rule": "R6", "tool": "Bash"}), encoding="utf-8")
    s = json.loads(run("diag", "--logs", logs, "--since", "1d", "--format", "json").stdout)
    assert s["max_parallel_subagents"] == 2
    row = next(r for r in s["rows"] if r["key"] == "ytfeed.yt_comments")
    assert row["calls"] == 2 and row["quota_units"] == 2 and row["p95_ms"] == 300 and row["approx_tokens"] == 2000
    assert s["guard_decisions"] == {"deny R6 Bash": 1}
    md = run("diag", "--logs", logs, "--by", "agent").stdout.decode("utf-8")
    assert "yt-media-feed-helper:scout" in md and "Max parallel" in md
