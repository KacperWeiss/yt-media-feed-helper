"""Round 2 features: tokeninfo, Q-24, quality metadata, anchors, mixed sample,
log(1+likes) weight, deduplication, stance_stats, strict_deps/hashes."""
import importlib.util
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import call

SERVER = Path(__file__).resolve().parent.parent / "server"


def _launch():
    spec = importlib.util.spec_from_file_location("launch_r2", SERVER / "launch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- §5a-9: tokeninfo once per session ---------------------------------------------------------------------
def _token(path):
    from ytfeed.auth import SCOPE

    path.write_text(json.dumps({"token": "ya29.x", "refresh_token": "1//x", "client_id": "c", "client_secret": "s", "scopes": [SCOPE],
                                "token_uri": "https://oauth2.googleapis.com/token", "expiry": "2999-01-01T00:00:00Z"}), encoding="utf-8")


def test_tokeninfo_verified_once_per_session(env, monkeypatch):
    from ytfeed import auth

    _token(env["secrets"] / "token.json")
    calls = []
    monkeypatch.setattr(auth, "tokeninfo_scopes", lambda tok, timeout=10.0: calls.append(tok) or {auth.SCOPE})
    a = auth.AuthManager(token_file=env["secrets"] / "token.json", mock=False)
    monkeypatch.setattr(a, "access_token", lambda: "ya29.x")
    for _ in range(3):
        st = a.status(remote=True)
    assert st["scope_verified_remote"] is True and st["scope_ok"] and len(calls) == 1
    assert "ya29" not in json.dumps(st)


def test_tokeninfo_mismatch_and_network_error(env, monkeypatch):
    from ytfeed import auth

    _token(env["secrets"] / "token.json")
    a = auth.AuthManager(token_file=env["secrets"] / "token.json", mock=False)
    monkeypatch.setattr(a, "access_token", lambda: "ya29.x")
    monkeypatch.setattr(auth, "tokeninfo_scopes", lambda tok, timeout=10.0: (_ for _ in ()).throw(OSError("offline")))
    st = a.status(remote=True)
    assert st["scope_verified_remote"] is None and "offline" in st["remote_check"] and st["scope_ok"]  # no network ≠ refusal
    monkeypatch.setattr(auth, "tokeninfo_scopes", lambda tok, timeout=10.0: {auth.SCOPE, "https://www.googleapis.com/auth/youtube"})
    st = a.status(remote=True)
    assert st["scope_verified_remote"] is False and st["scope_ok"] is False and st["authorized"] is False and "hint" in st


def test_tokeninfo_mock_no_network(app, monkeypatch):
    from ytfeed import auth

    monkeypatch.setattr(auth, "tokeninfo_scopes", lambda *a, **k: pytest.fail("network in mock mode"))
    d = call(app, "auth_status")["data"]
    assert d["mock"] and d["scope_verified_remote"] is None and "workspace" in d


def test_doctor_mock_skips_tokeninfo(env):
    e = dict(os.environ)
    cp = subprocess.run([sys.executable, "-I", str(SERVER / "launch.py"), "doctor", "--data-dir", str(env["tmp"] / "d")], capture_output=True, env=e)
    checks = {c["name"]: c for c in json.loads(cp.stdout.decode("utf-8"))["checks"]}
    assert checks["token_scope_remote"]["ok"] and "mock" in checks["token_scope_remote"]["detail"]


# ---- §5a-10 (Q-24): numbers require a ref --------------------------------------------------------------------
def _seed(app):
    app.evidence.register("yt:v:NUMVID00001", title="t", published_at="2026-11-01T00:00:00Z", channel="UCx")


def _report(extra_disc, extra_tldr=""):
    return ("# T\n\n## TL;DR\n<!-- section:tldr -->\n\n- Fact [[yt:v:NUMVID00001]]\n" + extra_tldr +
            "\n## Discrepancies and uncertainties\n<!-- section:discrepancies -->\n\n" + extra_disc +
            "\n\n## Methodology and limitations\n<!-- section:methodology -->\n\nAnalyzed 12 comments.\n")


@pytest.mark.parametrize("disc,tldr,ok", [
    ("No contradictions between sources.", "", True),
    ("Channel A says November 12, channel B November 15.", "", False),
    ("Channel A says November 12 [[yt:v:NUMVID00001]].", "", True),
    ("- 3 of 5 creators disagree", "", False),
    ("None.", "_About 40% of comments were negative._\n", False),
    ("None.", "_A note without numbers._\n", True),
])
def test_q24_numeric_lines_need_ref(app, disc, tldr, ok):
    _seed(app)
    r = call(app, "report_save", topic_slug="num-test", title="t", markdown=_report(disc, tldr))
    assert r["ok"] is ok, r
    if not ok:
        assert "numeric_claim_without_ref" in {v["rule"] for v in r["error"]["violations"]}


# ---- §5b-8/9: anchors and quality metadata ----------------------------------------------------------------------
def test_quality_frontmatter_and_anchors(app):
    _seed(app)
    call(app, "quota_estimate", plan=[], begin_report=True)
    q = {"sample_sizes": {"YouTube": 120, "YouTube PL": 25}, "lang_unknown": 7, "pages_scanned": 4, "coverage": {"channels": 3}}
    r = call(app, "report_save", topic_slug="q-meta", title="t", markdown=_report("None."), quality=q,
             chat_summary="\n".join(f"- p{i}" for i in range(8)))
    assert r["ok"], r
    text = (app.ws.root / r["data"]["path"]).read_text(encoding="utf-8")
    assert "quality:" in text and "lang_unknown: 7" in text and "budget_used: 0" in text
    assert r["data"]["anchors"]["TL;DR"].endswith(".md#tldr")
    assert r["data"]["anchors"]["Discrepancies and uncertainties"].endswith("#discrepancies-and-uncertainties")
    assert any("3–5 bullets" in w for w in r["warnings"])
    pl = _report("Brak.").replace("Discrepancies and uncertainties", "Rozbieżności i niepewności")
    r_pl = call(app, "report_save", topic_slug="q-meta-pl", title="t", markdown=pl, language="pl")
    assert r_pl["data"]["anchors"]["Rozbieżności i niepewności"].endswith("#rozbieżności-i-niepewności")  # non-ASCII anchors
    bad = call(app, "report_save", topic_slug="q-bad", title="t", markdown=_report("None."), quality={"evil": 1})
    assert "bad_quality_metadata" in {v["rule"] for v in bad["error"]["violations"]}
    bad2 = call(app, "report_save", topic_slug="q-bad2", title="t", markdown=_report("None."), quality={"notes": "https://evil.example/x"})
    assert "link_in_metadata" in {v["rule"] for v in bad2["error"]["violations"]}


# ---- §5b-2/3/6: mixed sample, weight, deduplication ----------------------------------------------------------------
def test_mixed_sample_and_weight(app):
    r = call(app, "yt_comments", video_id="pdxEU5dd001", order="mixed", max_threads=10)
    assert r["ok"], r
    cms = r["data"]["comments"]
    refs = [c["ref"] for c in cms]
    assert len(refs) == len(set(refs)) and {c["sampled_by"] for c in cms} <= {"relevance", "time"}
    assert r["data"]["sample"]["order"] == "mixed" and r["data"]["sample"]["mix"]["relevance"] >= 1
    assert all(c["weight"] == round(math.log1p(c["likes"]), 3) for c in cms)
    assert call(app, "yt_comments", video_id="pdxEU5dd001", order="mixed", cursor="abc")["error"]["code"] == "INVALID_ARG"


def test_copypasta_dedup(app, monkeypatch):
    def item(i, text, likes=0):
        return {"id": f"Ugdup{i:03d}", "snippet": {"channelId": "UCx", "totalReplyCount": 0, "topLevelComment": {"snippet": {
            "textOriginal": text, "likeCount": likes, "publishedAt": "2026-11-01T00:00:00Z", "authorDisplayName": f"a{i}",
            "authorChannelId": {"value": f"UCa{i}"}}}}}

    spam = "KUP TANIE KONTO STEAM NA MOJEJ STRONIE!!!"  # Polish spam comment (test data)
    data = {"items": [item(1, spam), item(2, "Świetna aktualizacja, wreszcie działa"), item(3, spam.lower() + " "), item(4, spam), item(5, "ok")]}
    monkeypatch.setattr(app.transport, "get", lambda api, endpoint, params: data)
    r = call(app, "yt_comments", video_id="dupVideo001")
    cms = r["data"]["comments"]
    assert len(cms) == 3 and r["data"]["sample"]["duplicates_merged"] == 2 and cms[0]["duplicates"] == 2


# ---- §5b-1/4: stance_stats --------------------------------------------------------------------------------------------
def test_stance_stats(app):
    r = call(app, "stance_stats", groups={"PL": {"positive": 10, "negative": 8, "mixed": 2, "neutral": 0},
                                          "ALL": {"positive": 40, "negative": 20, "mixed": 10, "neutral": 30,
                                                  "weights": {"positive": 50.0, "negative": 50.0}}})
    g = r["data"]["groups"]
    assert g["PL"]["n"] == 20 and g["PL"]["percent"] is None and g["PL"]["small_sample"] is True
    lo, hi = g["PL"]["wilson95"]["positive"]
    assert 28 < lo < 31 and 69 < hi < 72  # Wilson 10/20 ≈ 29.9–70.1
    assert g["ALL"]["percent"]["positive"] == 40.0 and g["ALL"]["weighted_percent"]["positive"] == 50.0
    assert call(app, "stance_stats", groups={"x": {"positive": -1}})["error"]["code"] == "INVALID_ARG"


# ---- S-08 round 2: strict_deps / hashes -----------------------------------------------------------------------------
def test_hashes_file_covers_lock():
    lock = [ln.split("==")[0].lower() for ln in (SERVER / "requirements.lock").read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")]
    hashes = {ln.split("==")[0].lower(): ln for ln in (SERVER / "requirements.hashes.txt").read_text(encoding="utf-8").splitlines()
              if ln and not ln.startswith("#")}
    assert set(lock) == set(hashes)
    assert all(ln.count("--hash=sha256:") == 1 for ln in hashes.values())
    assert "# [sdist]" in hashes["pyyaml"]  # no cp315 wheel — documented


def test_strict_deps_install_command(env, monkeypatch):
    L = _launch()
    seen = {}
    monkeypatch.setattr(L.subprocess, "call", lambda cmd, **kw: seen.setdefault("cmd", cmd) and 0)
    monkeypatch.setattr(L.venv, "EnvBuilder", lambda **kw: type("B", (), {"create": lambda self, d: (Path(d) / "Scripts").mkdir(parents=True, exist_ok=True)})())
    monkeypatch.setenv("YTFEED_STRICT_DEPS", "true")
    h_strict = L.requirements_hash()
    L.install(env["tmp"] / "dd", force=True)
    assert "--require-hashes" in seen["cmd"] and "--no-deps" in seen["cmd"] and seen["cmd"][-1].endswith("requirements.hashes.txt")
    monkeypatch.setenv("YTFEED_STRICT_DEPS", "")
    assert L.requirements_hash() != h_strict  # mode change = new venv
