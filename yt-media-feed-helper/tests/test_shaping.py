import json

import pytest

from ytfeed import shaping
from ytfeed.errors import ToolError
from ytfeed.sanitize import truncate


def test_envelope():
    env = json.loads(shaping.ok({"a": 1}, page={"returned": 1}, quota={"cost": 0}, warnings=["w", ""], untrusted=True))
    assert env["ok"] and env["data"] == {"a": 1} and env["warnings"] == ["w"] and "untrusted_notice" in env
    e = json.loads(shaping.err("NOT_FOUND", "missing", "hint"))
    assert e == {"ok": False, "error": {"code": "NOT_FOUND", "message": "missing", "hint": "hint"}}


def test_projection():
    item = {"ref": "yt:v:x", "views": 5, "likes": 1, "untrusted": {"title": "T", "description": "D"}, "injection_suspect": False}
    out = shaping.project(item, ["views", "untrusted.title"], {"views", "likes", "untrusted.title", "untrusted.description"})
    assert out == {"ref": "yt:v:x", "views": 5, "untrusted": {"title": "T"}, "injection_suspect": False}
    assert shaping.project(item, None, set()) is item
    with pytest.raises(ToolError):
        shaping.project(item, ["secret"], {"views"})


def test_truncate_suffix():
    assert truncate("abcdef", 4) == "abcd…[+2 chars]"
    assert truncate("abc", 4) == "abc"


def test_cursor_roundtrip_and_hash():
    params = {"q": "x", "n": 1}
    items = list(range(10))
    chunk, page = shaping.paginate(items, None, params, 4)
    assert chunk == [0, 1, 2, 3] and page["has_more"]
    chunk2, page2 = shaping.paginate(items, page["next_cursor"], params, 4)
    assert chunk2 == [4, 5, 6, 7]
    chunk3, page3 = shaping.paginate(items, page2["next_cursor"], params, 4)
    assert chunk3 == [8, 9] and not page3["has_more"] and page3["next_cursor"] is None
    with pytest.raises(ToolError):
        shaping.decode_cursor(page["next_cursor"], {"q": "other"})
    with pytest.raises(ToolError):
        shaping.decode_cursor("!!!garbage", params)
    c = shaping.encode_cursor(params, 0, page_token="PT1")
    assert shaping.decode_cursor(c, params)["pt"] == "PT1"


def test_clamp():
    assert shaping.clamp(500, 1, 200, 40) == 200
    assert shaping.clamp(None, 1, 200, 40) == 40
    assert shaping.clamp(0, 1, 200, 40) == 1
