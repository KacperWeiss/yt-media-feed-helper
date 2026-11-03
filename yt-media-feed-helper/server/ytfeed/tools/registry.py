"""Tool registry: the `tool` decorator turns exceptions into the `{"ok": false, ...}` error envelope."""
import functools
import logging
from typing import Callable

from .. import shaping
from ..errors import ToolError, UpstreamHTTPError

log = logging.getLogger("ytfeed.tools")

TOOLS: dict[str, Callable[..., str]] = {}


def tool(fn: Callable[..., str]) -> Callable[..., str]:
    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> str:
        try:
            return fn(*args, **kwargs)
        except ToolError as e:
            return shaping.err(e.code, e.message, e.hint, e.extra or None)
        except UpstreamHTTPError as e:
            return shaping.err("UPSTREAM_ERROR", f"HTTP {e.status} {e.reason}: {e.message[:200]}", "Try again later or change the parameters.")
        except PermissionError as e:
            return shaping.err("INVALID_ARG", str(e))
        except Exception as e:  # noqa: BLE001 — the server must not crash on a single call
            log.exception("tool %s failed", fn.__name__)
            return shaping.err("UPSTREAM_ERROR", f"{type(e).__name__}: {str(e)[:300]}")

    TOOLS[fn.__name__] = wrapper
    return wrapper
