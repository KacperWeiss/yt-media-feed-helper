import json
import os

import pytest
import yaml

from ytfeed.cache import Store
from ytfeed.catalog import proposals, store, taxonomy
from ytfeed.errors import ToolError
from ytfeed.evidence import EvidenceRegistry
from ytfeed.paths import atomic_write_text

PLS = "UCpolskistrateg000000002"
INJ = "UClegitgaming00000000005"


@pytest.fixture
def ev(env):
    store = Store(env["data"] / "cache.sqlite")
    reg = EvidenceRegistry(store)
    reg.register(f"yt:ch:{PLS}", title="Polski Strateg")
    reg.register(f"yt:ch:{INJ}", title="Legit")
    yield reg
    store.conn.close()


def V(value, conf=0.8, refs=None):
    return {"value": value, "confidence": conf, "rationale": "test", "evidence_refs": refs or []}


def test_propose_skips_locked_and_validates(ws, ev):
    idx = taxonomy.load(ws)
    res = proposals.propose(ws, idx, ev, PLS, {
        "specialization": [V("Hearts of Iron")],          # locked (Strategy: user)
        "subscription": [V("Supporter")],                # locked (user)
        "communication": [V("Official", 0.4, [f"yt:ch:{PLS}"])],
        "content": [V("Board games")],                      # not in the taxonomy
        "channel_type": [V("Streamer", refs=["yt:v:unknownvid1"])],  # unknown ref
    }, [], None)
    assert set(res["skipped_locked"]) == {"specialization", "subscription"}
    assert res["accepted_dims"] == ["communication"]
    reasons = {i["dimension"]: i["reason"] for i in res["invalid"]}
    assert "not in the taxonomy" in reasons["content"] and "evidence_refs" in reasons["channel_type"]
    saved = proposals.read(ws, PLS)
    assert saved["status"] == "pending" and saved["proposal_id"] == res["proposal_id"]


def test_agent_cannot_set_supporter(ws, ev):
    idx = taxonomy.load(ws)
    import pytest

    from ytfeed.errors import ToolError

    with pytest.raises(ToolError) as e:  # Q-16: errors only → INVALID_ARG, no empty proposal saved
        proposals.propose(ws, idx, ev, INJ, {"subscription": [V("Supporter")]}, [], None)
    assert e.value.code == "INVALID_ARG" and e.value.extra["invalid"][0]["reason"].startswith("value can only be set manually")
    assert proposals.read(ws, INJ) is None


def test_new_values_only_open_dims(ws, ev):
    idx = taxonomy.load(ws)
    res = proposals.propose(ws, idx, ev, INJ, {"specialization": [V("Crusader Kings")]},
                            [{"dimension": "specialization", "value": "Crusader Kings", "rationale": "3/12"},
                             {"dimension": "content", "value": "Music", "rationale": "x"}], None)
    assert res["accepted_dims"] == ["specialization"]
    assert any(i["dimension"] == "content" for i in res["invalid"])
    with pytest.raises(ToolError):
        proposals.propose(ws, idx, ev, INJ, {}, [{"dimension": "specialization", "value": f"X{i}"} for i in range(4)], None)


def test_commit_overrides_become_user_and_agent_never_overwrites_user(ws, ev):
    idx = taxonomy.load(ws)
    pid = proposals.propose(ws, idx, ev, INJ, {
        "channel_type": [V("YouTube creator")], "content": [V("Gaming")], "communication": [V("Unofficial")],
        "language_region": [V("EN")], "specialization": [V("Crusader Kings")]},
        [{"dimension": "specialization", "value": "Crusader Kings", "rationale": "3/12 videos about CK3"}], None)["proposal_id"]
    res = proposals.commit(ws, [{"proposal_id": pid, "action": "accept_with_changes", "overrides": {"communication": ["Media partner"]}}],
                           [{"dimension": "specialization", "value": "Crusader Kings"}], "ok")
    assert res["committed"] == 1 and res["taxonomy_added"] == ["specialization: Crusader Kings"]
    e = store.load(ws)["channels"][INJ]
    assert e["status"] == "approved"
    assert e["categories"]["communication"] == [{"value": "Media partner", "source": "user", "locked": True, "set_at": e["categories"]["communication"][0]["set_at"]}]
    assert e["categories"]["specialization"][0]["value"] == "Crusader Kings"
    assert taxonomy.load(ws).version == 2
    # a second agent proposal does not touch user values
    pid2 = proposals.propose(ws, taxonomy.load(ws), ev, INJ, {"communication": [V("Official")], "content": [V("History")]}, [], None)
    assert "communication" in pid2["skipped_locked"]
    proposals.commit(ws, [{"proposal_id": pid2["proposal_id"], "action": "accept"}], [], None)
    e = store.load(ws)["channels"][INJ]
    assert e["categories"]["communication"][0]["value"] == "Media partner"
    assert e["categories"]["content"][0]["value"] == "History"
    hist = [json.loads(line) for line in ws.history_file.read_text(encoding="utf-8").splitlines()]
    assert [h["event"] for h in hist] == ["accept_with_changes", "taxonomy_add", "accept"]


def test_reject_and_stale(ws, ev):
    idx = taxonomy.load(ws)
    pid = proposals.propose(ws, idx, ev, INJ, {"content": [V("Gaming")]}, [], None)["proposal_id"]
    res = proposals.commit(ws, [{"proposal_id": pid, "action": "reject"}], [], None)
    assert res["rejected"] == 1
    assert store.load(ws)["channels"][INJ]["status"] == "uncategorized"
    res = proposals.commit(ws, [{"proposal_id": pid, "action": "accept"}], [], None)
    assert res["committed"] == 0 and res["warnings"]


def test_missing_required_keeps_pending(ws, ev):
    idx = taxonomy.load(ws)
    pid = proposals.propose(ws, idx, ev, INJ, {"content": [V("Gaming")]}, [], None)["proposal_id"]
    res = proposals.commit(ws, [{"proposal_id": pid, "action": "accept"}], [], None)
    assert store.load(ws)["channels"][INJ]["status"] == "pending"
    assert "missing required" in res["warnings"][0]


def test_set_manual_and_unlock(ws):
    idx = taxonomy.load(ws)
    store.set_manual(ws, idx, INJ, {"subscription": ["Supporter"]}, [])
    e = store.load(ws)["channels"][INJ]
    assert e["categories"]["subscription"][0]["source"] == "user"
    store.set_manual(ws, idx, INJ, {}, ["subscription"])
    assert store.load(ws)["channels"][INJ]["categories"]["subscription"][0]["locked"] is False
    with pytest.raises(ToolError):
        store.set_manual(ws, idx, INJ, {"communication": ["Official", "Unofficial"]}, [])


def test_atomic_write_survives_crash(ws, monkeypatch):
    before = ws.channels_file.read_text(encoding="utf-8")

    def boom(*a, **k):
        raise OSError("simulated crash")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        with store.transaction(ws) as doc:
            doc["channels"]["UCx"] = {"title": "x"}
    monkeypatch.undo()
    assert ws.channels_file.read_text(encoding="utf-8") == before
    assert not list(ws.catalog.glob(".channels.yaml.*.tmp"))
    yaml.safe_load(before)


def test_whitelist(ws, tmp_path):
    with pytest.raises(PermissionError):
        atomic_write_text(tmp_path / "outside.txt", "x", ws)
