import json

import pytest

from conftest import FIXTURES, hook_json, run_hook

# Polish prompts (functional data: the classifier keeps Polish trigger keywords).
QUERIES_PL = [
    "Daj mi najnowsze oficjalne ogłoszenia twórców z kategorii strategia.",
    "Daj mi oficjalne informacje na temat książek Brandona Sandersona.",
    "Sprawdź ogólny odbiór community nadchodzącego DLC do Europa Universalis 5.",
    "Podsumuj odbiór przez polskich fanów EU5 najnowszej zmiany eventów powiązanych z utworzeniem Rzeczypospolitej Obojga Narodów.",
]
QUERIES_EN = [
    "Give me the latest official announcements from creators in the strategy category.",
    "Give me official news about Brandon Sanderson's books.",
    "Check the overall community reception of the upcoming Europa Universalis 5 DLC.",
    "Summarize how Polish fans received the latest EU5 change to the Polish-Lithuanian Commonwealth formation events.",
    "What's new on my subscribed channels?",
    "Write a report on opinions about the Hearts of Iron patch.",
]


def ctx_for(wsdir, prompt, **env):
    cp = run_hook("info_mode_prompt.py", {"hook_event_name": "UserPromptSubmit", "session_id": "s", "cwd": str(wsdir), "prompt": prompt}, env)
    assert cp.returncode == 0
    out = hook_json(cp)
    return out["hookSpecificOutput"]["additionalContext"] if out else None


@pytest.mark.parametrize("q", QUERIES_PL + QUERIES_EN)
def test_report_queries_get_info_mode(ws, q):
    c = ctx_for(ws.root, q)
    assert c and "Info mode" in c and "catalog_query" in c and "language of the user's question" in c


@pytest.mark.parametrize("name", ["userprompt_report_pl.json", "userprompt_report_en.json"])
def test_fixture_payload(ws, name):
    p = json.loads((FIXTURES / "hooks" / name).read_text(encoding="utf-8").replace("WORKSPACE", str(ws.root).replace("\\", "\\\\")))
    out = hook_json(run_hook("info_mode_prompt.py", p))
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "Info mode" in out["hookSpecificOutput"]["additionalContext"]


def test_english_and_taxonomy_terms(ws):
    assert ctx_for(ws.root, "What's new from Paradox?")
    assert ctx_for(ws.root, "Hearts of Iron — co u twórców?")  # taxonomy term


@pytest.mark.parametrize("q", ["Zasubskrybuj kanał Paradoxu na YouTube", "Please subscribe me to this channel", "Dodaj do listy życzeń EU5 na Steam"])
def test_mutation_intent(ws, q):
    c = ctx_for(ws.root, q)
    assert c and "read-only" in c and "link" in c


@pytest.mark.parametrize("q", ["Napraw błąd w funkcji parse_args", "ile to 2+2?", "Refactor the auth module", "Fix the bug in parse_args"])
def test_neutral_prompt_silent(ws, q):
    assert ctx_for(ws.root, q) is None


def test_outside_workspace_requires_domain(env):
    other = env["tmp"] / "o"
    other.mkdir()
    assert ctx_for(other, "Sprawdź testy jednostkowe", YTFEED_WORKSPACE="") is None
    assert ctx_for(other, "Sprawdź najnowsze filmy na YouTube o EU5", YTFEED_WORKSPACE="")
    assert ctx_for(other, "Check the unit tests", YTFEED_WORKSPACE="") is None
    assert ctx_for(other, "Check the latest YouTube videos about EU5", YTFEED_WORKSPACE="")
