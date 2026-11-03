"""Rules §4.2 — MCP (ytfeed.catalog.rules) and the validate_catalog.py script give identical results."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from ytfeed.catalog import rules

PLUGIN = Path(__file__).resolve().parent.parent
TEMPLATE = PLUGIN / "templates" / "taxonomy.default.yaml"
SCRIPT = PLUGIN / "skills" / "taxonomy-edit" / "scripts" / "validate_catalog.py"


@pytest.fixture
def tax():
    return yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))


def ch(**cats):
    return {"title": "T", "status": "approved", "categories": cats}


def A(v, conf=0.9, rationale="r"):
    return {"value": v, "source": "agent", "confidence": conf, "rationale": rationale}


def U(v):
    return {"value": v, "source": "user", "locked": True}


FULL = dict(channel_type=[A("Streamer")], content=[A("Gaming")], communication=[A("Unofficial")],
            subscription=[A("Subscriber")], language_region=[A("PL")])


def codes(issues, level=None):
    return {i["code"] for i in issues if level is None or i["level"] == level}


def test_template_valid(tax):
    assert [i for i in rules.validate_taxonomy(tax) if i["level"] == "error"] == []


def test_rule1_required(tax):
    c = copy.deepcopy(FULL)
    del c["language_region"]
    assert "REQUIRED_MISSING" in codes(rules.validate_channels(tax, {"channels": {"UC1": ch(**c)}}))
    pending = ch(**c) | {"status": "pending"}
    assert "REQUIRED_MISSING" not in codes(rules.validate_channels(tax, {"channels": {"UC1": pending}}))


def test_rule2_single(tax):
    c = copy.deepcopy(FULL) | {"communication": [A("Official"), A("Unofficial")]}
    assert "CARDINALITY_SINGLE" in codes(rules.validate_channels(tax, {"channels": {"UC1": ch(**c)}}))


def test_rule3_values_and_synonyms(tax):
    c = copy.deepcopy(FULL) | {"specialization": [A("EU5"), A("Szachy")]}
    iss = rules.validate_channels(tax, {"channels": {"UC1": ch(**c)}})
    assert "SYNONYM_USED" in codes(iss, "warning") and "UNKNOWN_VALUE" in codes(iss, "error")


def test_rule4_source(tax):
    c = copy.deepcopy(FULL) | {"content": [{"value": "Gaming", "source": "agent"}], "channel_type": [{"value": "Streamer", "source": "bot"}]}
    assert {"AGENT_CONFIDENCE", "AGENT_RATIONALE", "BAD_SOURCE"} <= codes(rules.validate_channels(tax, {"channels": {"UC1": ch(**c)}}))


def test_rule5_supporter_only_user(tax):
    c = copy.deepcopy(FULL) | {"subscription": [A("Supporter")]}
    assert "AGENT_NOT_SETTABLE" in codes(rules.validate_channels(tax, {"channels": {"UC1": ch(**c)}}))
    c2 = copy.deepcopy(FULL) | {"subscription": [U("Supporter")]}
    assert rules.validate_channels(tax, {"channels": {"UC1": ch(**c2)}}) == []


def test_rule6_stale(tax):
    tax["taxonomy_version"] = 10
    e = ch(**FULL) | {"taxonomy_version_at_categorization": 2}
    assert "STALE_CATEGORIZATION" in codes(rules.validate_channels(tax, {"channels": {"UC1": e}}), "warning")


def test_taxonomy_structure_errors(tax):
    bad = copy.deepcopy(tax)
    bad["dimensions"]["content"]["cardinality"] = "many"
    bad["dimensions"]["specialization"]["values"].append({"key": "Lore"})
    bad["dimensions"]["specialization"]["values"].append({"key": "X", "parent": "Nope"})
    bad["aliases"]["zle"] = {"dimension": "content", "value": "Nope"}
    assert {"DIM_CARDINALITY", "VALUE_DUPLICATE", "VALUE_BAD_PARENT", "ALIAS_BAD_TARGET"} <= codes(rules.validate_taxonomy(bad))


def test_script_matches_mcp(ws):
    """validate_catalog.py (skill without MCP) == rules.validate_workspace (MCP) on the same fixtures."""
    doc = yaml.safe_load(ws.channels_file.read_text(encoding="utf-8"))
    doc["channels"]["UCbad"] = ch(**(copy.deepcopy(FULL) | {"communication": [A("Official"), A("X")], "subscription": [A("Supporter")]}))
    ws.channels_file.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    mcp_issues = rules.validate_workspace(ws.root)
    cp = subprocess.run([sys.executable, str(SCRIPT), "--workspace", str(ws.root), "--json"], capture_output=True)
    assert cp.returncode == 1
    script_issues = json.loads(cp.stdout.decode("utf-8"))["issues"]
    assert script_issues == mcp_issues
    assert {"CARDINALITY_SINGLE", "UNKNOWN_VALUE", "AGENT_NOT_SETTABLE"} <= codes(mcp_issues)
