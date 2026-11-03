import pytest

from ytfeed.catalog import query, store, taxonomy
from ytfeed.errors import ToolError

PDX, PLS, BSN, CZY, INJ, PRT = ("UCparadoxinteractive0001", "UCpolskistrateg000000002", "UCbrandonsanderson000003",
                                "UCczytelniarecenzji00004", "UClegitgaming00000000005", "UCstratpartner0000000006")


def ids(rows):
    return {r["channel_id"] for r in rows}


@pytest.fixture
def ctx(ws):
    return store.load(ws), taxonomy.load(ws)


def test_and_between_or_within(ctx):
    doc, idx = ctx
    rows = query.run(doc, idx, {"content": ["Gaming"], "communication": ["Official", "Community Manager"]})
    assert ids(rows) == {PDX}
    rows = query.run(doc, idx, {"communication": ["Official", "Media partner"]})
    assert ids(rows) == {PDX, BSN, PRT}


def test_exclude(ctx):
    doc, idx = ctx
    rows = query.run(doc, idx, {"content": ["Gaming"]}, exclude={"communication": ["Unofficial"]})
    assert ids(rows) == {PDX, PRT}


def test_hierarchy(ctx):
    doc, idx = ctx
    # Strategy includes Grand strategy → Europa Universalis / Hearts of Iron
    rows = query.run(doc, idx, {"specialization": ["Strategy"]})
    assert ids(rows) == {PDX, PLS, PRT}


def test_synonyms_and_aliases(ctx):
    doc, idx = ctx
    assert ids(query.run(doc, idx, {"specialization": ["EU5"]})) == {PDX, PLS, PRT}
    assert ids(query.run(doc, idx, {"specialization": ["strategy"]})) == {PDX, PLS, PRT}
    assert ids(query.run(doc, idx, {"specialization": ["strategia"]})) == {PDX, PLS, PRT}  # Polish alias kept
    assert ids(query.run(doc, idx, {"content": ["books"]})) == {BSN, CZY}
    assert ids(query.run(doc, idx, {"content": ["ksiazki"]})) == {BSN, CZY}
    assert ids(query.run(doc, idx, {"communication": ["oficjalne"]})) == {PDX, BSN}


def test_min_confidence_vs_user(ctx):
    doc, idx = ctx
    # PLS: EU (agent 0.55) drops out, but Strategy (user) always matches
    assert ids(query.run(doc, idx, {"specialization": ["Europa Universalis"]}, min_confidence=0.6)) == {PDX, PRT}
    assert PLS in ids(query.run(doc, idx, {"specialization": ["Strategy"]}, min_confidence=0.99))
    assert ids(query.run(doc, idx, {"specialization": ["Sanderson"]}, min_confidence=0.6)) == {BSN}


def test_unknown_value_suggests(ctx):
    doc, idx = ctx
    with pytest.raises(ToolError) as e:
        query.run(doc, idx, {"communication": ["Official channels"]})
    assert e.value.code == "INVALID_ARG" and "Official" in e.value.hint
    with pytest.raises(ToolError):
        query.run(doc, idx, {"nonexistent": ["x"]})


def test_status_and_text(ctx):
    doc, idx = ctx
    assert ids(query.run(doc, idx, status="uncategorized")) == {INJ}
    assert len(query.run(doc, idx, status="any")) == 6
    assert ids(query.run(doc, idx, text="polski")) == {PLS}


def test_term_expansion(ctx):
    _, idx = ctx
    terms = idx.expand_terms(["RON"])
    for t in ("Rzeczpospolita", "Rzplita", "PLC", "Commonwealth"):
        assert t in terms
    assert "EU5" in idx.expand_terms(["Europa Universalis"])
