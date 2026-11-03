"""QuotaLedger (§3.7): buckets, reset at Pacific-time midnight, reserve, report budget.

State in `<data_dir>/quota.json`; every operation runs under a file lock (msvcrt on Windows / fcntl on POSIX).
Cost is reserved BEFORE the API call (`reserve`) and corrected afterwards (`adjust`).
Report budget: 2000 units by default; exceeding it → REPORT_BUDGET_EXCEEDED
(raising the budget = `quota_estimate(begin_report=true, budget_units=N)` → hook `ask`).
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from .clock import iso, now_utc, parse_iso
from .config import Config
from .errors import ToolError
from .paths import atomic_write_text, setting


@contextlib.contextmanager
def file_lock(lock_path: Path, timeout: float = 10.0) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a+b")
    deadline = time.monotonic() + timeout
    try:
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"lock timeout: {lock_path}")
                    time.sleep(0.02)
            try:
                yield
            finally:
                f.seek(0)
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"lock timeout: {lock_path}")
                    time.sleep(0.02)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        f.close()


BUDGET_BASELINE = 2000  # default report budget (units)


def budget_baseline() -> int:
    """Round 2 (§5a-2): a permanently raised budget only from userConfig.report_budget_units (YTFEED_REPORT_BUDGET_UNITS) —
    out of the model's reach; catalog/config.yaml can only lower the budget."""
    v = setting("report_budget_units")  # YTFEED_REPORT_BUDGET_UNITS (env) > userConfig
    try:
        n = int(float(v)) if v else 0
    except ValueError:
        n = 0
    return n if n > 0 else BUDGET_BASELINE


def effective_budget(cfg: Config) -> int:
    try:
        configured = int(cfg.get("reports.budget_units", BUDGET_BASELINE) or BUDGET_BASELINE)
    except (TypeError, ValueError):
        configured = BUDGET_BASELINE
    return configured if 0 < configured < BUDGET_BASELINE else budget_baseline()


# Q-02: catalog tools (sync, channel profile for the categorizer) do not count against the REPORT budget.
REPORT_EXEMPT_TOOLS = {"catalog_sync_subscriptions", "yt_channel_profile", "catalog_add_channel"}


# Tool cost estimates (for quota_estimate); args_hint may override counts.
def _tool_cost(tool: str, hint: dict, costs: dict) -> tuple[str, int]:
    c = lambda k: int(costs.get(k, 1))  # noqa: E731
    if tool == "yt_channel_profile":
        return "default", c("channels.list") + c("playlistItems.list")
    if tool == "yt_recent_uploads":
        ch = int(hint.get("channels", 1))
        vids = int(hint.get("videos", ch * 5))
        pages = int(hint.get("pages_per_channel", 1))
        return "default", ch * pages * c("playlistItems.list") + math.ceil(max(vids, 1) / 50) * c("videos.list")
    if tool == "yt_video_details":
        return "default", math.ceil(max(int(hint.get("videos", 1)), 1) / 50) * c("videos.list")
    if tool == "yt_comments":
        return "default", math.ceil(max(int(hint.get("max_threads", 100)), 1) / 100) * c("commentThreads.list")
    if tool == "yt_search":
        return "search", c("search.list")
    if tool == "catalog_sync_subscriptions":
        subs = int(hint.get("subscriptions", 300))
        new = int(hint.get("new", 50))
        return "default", math.ceil(subs / 50) * c("subscriptions.list") + math.ceil(new / 50) * c("channels.list")
    return "default", 0


class QuotaLedger:
    def __init__(self, data_dir: Path, config: Config):
        self.path = data_dir / "quota.json"
        self.lock = data_dir / "quota.lock"
        self.cfg = config

    # ---- czas PT -------------------------------------------------------
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.cfg.get("quota.timezone", "America/Los_Angeles"))

    def day_pt(self, now: datetime | None = None) -> str:
        return (now or now_utc()).astimezone(self.tz()).date().isoformat()

    def resets_at(self, now: datetime | None = None) -> str:
        local = (now or now_utc()).astimezone(self.tz())
        nxt = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=self.tz())
        return iso(nxt)  # type: ignore[return-value]

    # ---- stan ----------------------------------------------------------
    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _fresh(self, state: dict) -> dict:
        day = self.day_pt()
        if state.get("day_pt") != day:
            report = state.get("report")
            state = {"day_pt": day, "buckets": {}, "by_tool": {}}
            if report and report.get("active"):
                # Q-02: the report budget does NOT survive the daily reset (an abandoned budget does not block later days)
                state["report"] = {**report, "active": False, "ended_at": iso(now_utc()), "end_reason": "day_reset"}
        state.setdefault("buckets", {})
        state.setdefault("by_tool", {})
        rep = state.get("report")
        if rep and rep.get("active"):
            ttl_min = float(self.cfg.get("reports.budget_ttl_minutes", 120) or 120)
            try:
                age_min = (now_utc() - parse_iso(rep.get("started_at") or "")).total_seconds() / 60
            except (ValueError, TypeError):
                age_min = ttl_min + 1
            if age_min > ttl_min:  # Q-02: report budget TTL (default 2 h)
                rep.update(active=False, ended_at=iso(now_utc()), end_reason=f"ttl_{int(ttl_min)}min")
        return state

    def _save(self, state: dict) -> None:
        atomic_write_text(self.path, json.dumps(state, ensure_ascii=False, indent=1))

    def _bucket_cfg(self, bucket: str) -> dict:
        return self.cfg.get(f"quota.buckets.{bucket}", {}) or {}

    def resolve_bucket(self, method: str) -> tuple[str, int, int]:
        """(bucket, units in the bucket, units counted toward the report budget)."""
        costs = self.cfg.get("quota.costs", {})
        units = int(costs.get(method, 1))
        if method == "search.list" and self._bucket_cfg("search").get("enabled"):
            return "search", 1, units
        return "default", units, units

    def _snapshot(self, state: dict, bucket: str, cost: int) -> dict:
        b = state["buckets"].get(bucket, {})
        bc = self._bucket_cfg(bucket)
        used = int(b.get("used", 0))
        limit = int(bc.get("daily_limit", 10000))
        snap = {"cost": cost, "bucket": bucket, "used_today": used, "limit": limit, "remaining": max(0, limit - used)}
        rep = state.get("report")
        if rep and rep.get("active"):
            snap["report_budget"] = {"used": rep.get("used", 0), "budget": rep.get("budget")}
        return snap

    # ---- operations ------------------------------------------------------
    def reserve(self, tool: str, method: str, count: int = 1) -> dict:
        """Reserves the cost of `count` calls of an API method. Raises ToolError at the reserve/budget."""
        bucket, per_call, report_units_per_call = self.resolve_bucket(method)
        units = per_call * count
        report_units = report_units_per_call * count
        with file_lock(self.lock):
            state = self._fresh(self._load())
            b = state["buckets"].setdefault(bucket, {"used": 0})
            bc = self._bucket_cfg(bucket)
            limit = int(bc.get("daily_limit", 10000))
            reserve = int(bc.get("reserve", 0))
            if units > 0 and b.get("used", 0) + units > limit - reserve:
                raise ToolError(
                    "QUOTA_RESERVE_REACHED",
                    f"Bucket '{bucket}': used {b.get('used', 0)}/{limit}, reserve {reserve}; the operation needs {units}.",
                    f"Quota resets at {self.resets_at()} (midnight PT). Local tools (cost 0) still work.",
                )
            rep = state.get("report")
            if report_units > 0 and rep and rep.get("active") and tool not in REPORT_EXEMPT_TOOLS:
                if rep.get("used", 0) + report_units > int(rep.get("budget", 0)):
                    raise ToolError(
                        "REPORT_BUDGET_EXCEEDED",
                        f"Report budget: {rep.get('used', 0)}/{rep.get('budget')} units; the operation needs {report_units} units.",
                        "Ask the user for consent and call quota_estimate(begin_report=true, budget_units=<new>) — a permission prompt will appear.",
                    )
                rep["used"] = rep.get("used", 0) + report_units
            b["used"] = b.get("used", 0) + units
            bt = state["by_tool"].setdefault(tool, 0)
            state["by_tool"][tool] = bt + report_units
            self._save(state)
            return self._snapshot(state, bucket, units)

    def adjust(self, tool: str, method: str, delta_count: int) -> None:
        """Correction after the call (e.g. fewer pages than reserved: negative delta)."""
        if delta_count == 0:
            return
        bucket, per_call, rep_per_call = self.resolve_bucket(method)
        with file_lock(self.lock):
            state = self._fresh(self._load())
            b = state["buckets"].setdefault(bucket, {"used": 0})
            b["used"] = max(0, b.get("used", 0) + per_call * delta_count)
            state["by_tool"][tool] = max(0, state["by_tool"].get(tool, 0) + rep_per_call * delta_count)
            rep = state.get("report")
            if rep and rep.get("active") and tool not in REPORT_EXEMPT_TOOLS:
                rep["used"] = max(0, rep.get("used", 0) + rep_per_call * delta_count)
            self._save(state)

    def mark_exhausted(self, bucket: str = "default") -> None:
        """The API returned 403 quotaExceeded → used = limit until the reset."""
        with file_lock(self.lock):
            state = self._fresh(self._load())
            limit = int(self._bucket_cfg(bucket).get("daily_limit", 10000))
            state["buckets"].setdefault(bucket, {})["used"] = limit
            state["buckets"][bucket]["exhausted_by_api"] = True
            self._save(state)

    def begin_report(self, budget: int | None = None) -> dict:
        b = int(budget or effective_budget(self.cfg))
        with file_lock(self.lock):
            state = self._fresh(self._load())
            state["report"] = {"active": True, "budget": b, "used": 0, "started_at": iso(now_utc())}
            self._save(state)
            return dict(state["report"])

    def end_report(self) -> dict | None:
        with file_lock(self.lock):
            state = self._fresh(self._load())
            rep = state.get("report")
            if rep and rep.get("active"):
                rep["active"] = False
                rep["ended_at"] = iso(now_utc())
                self._save(state)
                return dict(rep)
            return None

    def status(self) -> dict:
        with file_lock(self.lock):
            state = self._fresh(self._load())
        buckets: dict[str, Any] = {}
        for name in ("default", "search"):
            bc = self._bucket_cfg(name)
            used = int(state["buckets"].get(name, {}).get("used", 0))
            if name == "default":
                buckets[name] = {"used": used, "limit": int(bc.get("daily_limit", 10000)), "reserve": int(bc.get("reserve", 0))}
                lim = buckets[name]["limit"]
                if lim and used >= lim * float(bc.get("warn_at", 0.8)):
                    buckets[name]["warning"] = "warning threshold exceeded"
            else:
                buckets[name] = {"calls": used, "limit": int(bc.get("daily_limit", 100)), "enabled": bool(bc.get("enabled"))}
        top = sorted(({"tool": t, "units": u} for t, u in state["by_tool"].items() if u), key=lambda x: -x["units"])[:5]
        out = {"day_pt": state["day_pt"], "buckets": buckets, "top_consumers": top, "resets_at": self.resets_at()}
        if state.get("report"):
            out["report_budget"] = state["report"]
        return out

    def estimate(self, plan: list[dict]) -> dict:
        costs = self.cfg.get("quota.costs", {})
        total = 0
        search_calls = 0
        breakdown = []
        for step in plan:
            tool = str(step.get("tool", ""))
            count = int(step.get("count", 1))
            bucket, units = _tool_cost(tool, step.get("args_hint") or {}, costs)
            if bucket == "search":
                if self._bucket_cfg("search").get("enabled"):
                    search_calls += count
                    breakdown.append({"tool": tool, "count": count, "search_calls": count})
                    continue
                bucket = "default"
            total += units * count
            breakdown.append({"tool": tool, "count": count, "units": units * count})
        st = self.status()
        d = st["buckets"]["default"]
        after = d["limit"] - d["used"] - total
        report_units = total + search_calls * int(costs.get("search.list", 100))
        budget = effective_budget(self.cfg)
        return {
            "estimated_units": total,
            "search_calls": search_calls,
            "fits": after >= d["reserve"],
            "after": after,
            "report_budget": budget,
            "fits_report_budget": report_units <= budget,
            "breakdown": breakdown,
        }
