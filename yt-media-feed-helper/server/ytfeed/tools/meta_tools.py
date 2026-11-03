"""Meta tools (§3.6): quota, authorization, evidence registry."""
import math
from typing import Any

from pydantic import StrictBool, StrictInt

from .. import shaping
from ..context import get_ctx
from ..errors import ToolError
from .registry import tool


@tool
def quota_status() -> str:
    """YouTube quota status: usage per bucket (default/search), reserve, top consumers, current report budget, reset time (midnight PT). Cost 0."""
    ctx = get_ctx()
    return shaping.ok({**ctx.ledger.status(), "workspace": str(ctx.ws.root)})  # Q-13: workspace path for skill scripts


@tool
def quota_estimate(plan: list[dict[str, Any]], begin_report: StrictBool = False, budget_units: StrictInt | None = None,
                   end_report: StrictBool = False) -> str:
    """Estimates the cost of a plan [{tool,count,args_hint}] vs the quota and the report budget (default 2000 units). begin_report=true opens the budget (hard limit, expires after TTL); budget_units above the default requires approval (hook ask); end_report=true closes an abandoned budget."""
    ctx = get_ctx()
    if not isinstance(plan, list):
        raise ToolError("INVALID_ARG", "plan must be a list")
    if not isinstance(begin_report, bool) or not isinstance(end_report, bool) or (budget_units is not None and (isinstance(budget_units, bool) or not isinstance(budget_units, int))):
        raise ToolError("INVALID_ARG", "begin_report/end_report must be bool, budget_units an integer")  # S-05
    if end_report:
        closed = ctx.ledger.end_report()
        return shaping.ok({"report_closed": closed})
    est = ctx.ledger.estimate(plan)
    if budget_units is not None and budget_units < 1:
        raise ToolError("INVALID_ARG", "budget_units must be > 0")
    if begin_report:
        rep = ctx.ledger.begin_report(budget_units)
        est["report_budget"] = rep["budget"]
        est["fits_report_budget"] = est["estimated_units"] <= rep["budget"]
        est["report_started"] = rep
    warnings = []
    if not est["fits_report_budget"]:
        warnings.append("The plan exceeds the report budget — reduce the scope or ask the user to approve a higher budget.")
    if not est["fits"]:
        warnings.append("The plan does not fit the daily quota (with reserve).")
    return shaping.ok(est, warnings=warnings)


@tool
def auth_status() -> str:
    """YouTube authorization status: authorized, scopes, scope_ok (only youtube.readonly is allowed), token validity, mock. Never returns tokens. Cost 0."""
    ctx = get_ctx()
    return shaping.ok({**ctx.auth.status(remote=True), "workspace": str(ctx.ws.root)})


STANCES = ("positive", "negative", "mixed", "neutral")


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, c - h) * 100, 1), round(min(1.0, c + h) * 100, 1)]


@tool
def stance_stats(groups: dict[str, dict[str, Any]], min_sample: int = 30) -> str:
    """Stance distribution for report tables: per group {positive,negative,mixed,neutral[,weights:{stance: sum of log(1+likes)}]} → n, shares % (only when n ≥ min_sample), 95% Wilson interval, weighted share. Take report numbers from here. Cost 0."""
    if not isinstance(groups, dict) or not groups or len(groups) > 30:
        raise ToolError("INVALID_ARG", "groups: 1–30 groups {name: {positive: n, ...}}")
    out = {}
    for name, g in groups.items():
        if not isinstance(g, dict):
            raise ToolError("INVALID_ARG", f"group {name}: expected an object")
        counts = {s: int(g.get(s) or 0) for s in STANCES}
        if any(v < 0 for v in counts.values()):
            raise ToolError("INVALID_ARG", f"group {name}: negative numbers")
        n = sum(counts.values())
        row: dict[str, Any] = {"n": n, "counts": counts, "small_sample": n < int(min_sample)}
        row["percent"] = {s: round(100 * counts[s] / n, 1) for s in STANCES} if n >= int(min_sample) else None
        row["wilson95"] = {s: wilson(counts[s], n) for s in STANCES} if n else None
        w = g.get("weights") or {}
        if isinstance(w, dict) and w:
            tw = sum(float(w.get(s) or 0) for s in STANCES)
            row["weighted_percent"] = {s: round(100 * float(w.get(s) or 0) / tw, 1) for s in STANCES} if tw > 0 and n >= int(min_sample) else None
        out[name] = row
    return shaping.ok({"groups": out, "min_sample": int(min_sample),
                       "note": "percent=None when n < min_sample: report counts with a small-sample note; wilson95 = 95% interval in %."})


@tool
def evidence_resolve(refs: list[str]) -> str:
    """Resolves refs (yt:v:…, yt:c:…, st:r:… etc., ≤100) to canonical URLs, titles and dates from the evidence registry; unknown ones go to 'unknown'. Cost 0."""
    if len(refs) > 100:
        raise ToolError("INVALID_ARG", "At most 100 refs")
    return shaping.ok(get_ctx().evidence.resolve(refs))
