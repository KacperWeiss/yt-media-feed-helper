"""Tool errors mapped to the `{"ok": false, "error": {...}}` envelope."""
from __future__ import annotations

CODES = {
    "QUOTA_RESERVE_REACHED",
    "REPORT_BUDGET_EXCEEDED",
    "AUTH_REQUIRED",
    "NOT_FOUND",
    "INVALID_ARG",
    "UPSTREAM_ERROR",
    "SCOPE_MISMATCH",
    "VALIDATION_FAILED",
}


class ToolError(Exception):
    def __init__(self, code: str, message: str, hint: str | None = None, extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.extra = extra or {}


class UpstreamHTTPError(Exception):
    """HTTP error from an API (status + reason, e.g. quotaExceeded, commentsDisabled)."""

    def __init__(self, status: int, reason: str = "", message: str = ""):
        super().__init__(f"HTTP {status} {reason}: {message}")
        self.status = status
        self.reason = reason
        self.message = message


class MockFixtureMissing(Exception):
    """Mock mode: no fixture for (endpoint, params) — an explicit error, never a silent empty result."""
