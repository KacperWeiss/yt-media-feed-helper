"""report_save (§3.6) — the only way to write a report (ADR-04)."""
from typing import Any

from pydantic import StrictBool

from .. import reports, shaping
from ..context import get_ctx
from ..errors import ToolError
from .registry import tool


@tool
def report_save(topic_slug: str, title: str, markdown: str, query_spec: dict[str, Any] | None = None, chat_summary: str = "",
                strict: StrictBool = True, quality: dict[str, Any] | None = None, language: str | None = None) -> str:
    """Validates and saves a report: claims cite known [[ref]]s, quotes cite a comment/review, no foreign links, methodology required. Mark each H2 with <!-- section:KEY --> on the next line (tldr, findings, official, semi_official, creator_voices, community_reception, steam, discrepancies, methodology). language: ISO 639-1 (default query_spec.report_language or en). Saves reports/YYYY-MM-DD-<slug>.md."""
    ctx = get_ctx()
    if not isinstance(strict, bool):
        raise ToolError("INVALID_ARG", "strict must be a bool")
    if len(chat_summary) > 1200:
        raise ToolError("INVALID_ARG", "chat_summary max 1200 characters")
    if language is not None and (not isinstance(language, str) or len(language) > 8):
        raise ToolError("INVALID_ARG", "language must be an ISO 639-1 code (e.g. en, pl)")
    status = ctx.ledger.status()
    rep = status.get("report_budget") or {}
    units = rep.get("used") if rep.get("active") else None
    res = reports.save(ctx.ws, ctx.evidence, topic_slug, title, markdown, query_spec or {}, units, strict, quality, language)
    if not res.pop("ok"):
        return shaping.err("VALIDATION_FAILED", "The report failed citation validation — fix the listed lines and retry.",
                           "Every paragraph/bullet in claim sections needs a [[ref]] from tool results; "
                           "mark each H2 with <!-- section:KEY --> on the next line.", res)
    ctx.ledger.end_report()
    res["chat_summary"] = chat_summary
    res["quota_units"] = units
    warnings = []
    if len([ln for ln in chat_summary.splitlines() if ln.strip()]) > 6:
        warnings.append("chat_summary: 3–5 bullets recommended, each linking to a report section (anchors).")
    return shaping.ok(res, warnings=warnings)
