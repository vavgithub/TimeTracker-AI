"""
Planner-first meeting mapping (Calls list tasks with start_date + due_date).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .client import ClickUpClient


@dataclass(frozen=True)
class PlannerEvent:
    task_id: str
    task_name: str
    start_ms: int
    end_ms: int
    parent_name: str | None = None


def _to_int(v) -> int | None:
    try:
        if v is None:
            return None
        return int(v)
    except Exception:
        return None


def _dt_to_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _session_ms_window(session: dict) -> tuple[int | None, int | None]:
    s = session.get("start")
    e = session.get("end")
    try:
        sdt = s if isinstance(s, datetime) else datetime.fromisoformat(str(s))
        edt = e if isinstance(e, datetime) else datetime.fromisoformat(str(e))
        return _dt_to_ms(sdt), _dt_to_ms(edt)
    except Exception:
        return None, None


def _overlap_minutes(a_start_ms: int, a_end_ms: int, b_start_ms: int, b_end_ms: int) -> float:
    start = max(a_start_ms, b_start_ms)
    end = min(a_end_ms, b_end_ms)
    if end <= start:
        return 0.0
    return (end - start) / 60000.0


def _walk_tasks_with_subtasks(tasks: list[dict], parent_name: str | None = None) -> list[tuple[dict, str | None]]:
    out: list[tuple[dict, str | None]] = []
    for t in tasks or []:
        out.append((t, parent_name))
        for st in t.get("subtasks") or []:
            out.extend(_walk_tasks_with_subtasks([st], t.get("name") or parent_name))
    return out


def get_calls_planner_events(calls_list_id: str, date_str: str) -> list[PlannerEvent]:
    """
    Fetch Calls list tasks + subtasks, then extract anything with start_date & due_date
    that intersects the requested day (local day string is used only for filtering).
    """
    cu = ClickUpClient()
    if not cu.token or not calls_list_id:
        return []

    tasks = cu.get_list_tasks(calls_list_id, include_closed=True, subtasks=True)
    flat = _walk_tasks_with_subtasks(tasks)

    day_start = datetime.fromisoformat(f"{date_str}T00:00:00+05:30")
    day_end = datetime.fromisoformat(f"{date_str}T23:59:59+05:30")
    day_start_ms = _dt_to_ms(day_start)
    day_end_ms = _dt_to_ms(day_end)

    events: list[PlannerEvent] = []
    for t, parent_name in flat:
        tid = t.get("id")
        name = (t.get("name") or "").strip()
        s_ms = _to_int(t.get("start_date"))
        e_ms = _to_int(t.get("due_date"))
        if tid is None or not name or s_ms is None or e_ms is None:
            continue

        if not (s_ms < day_end_ms and e_ms > day_start_ms):
            continue

        events.append(
            PlannerEvent(
                task_id=str(tid),
                task_name=name,
                start_ms=s_ms,
                end_ms=e_ms,
                parent_name=parent_name,
            )
        )

    return events


def apply_planner_to_sessions(
    sessions: list[dict],
    *,
    calls_list_id: str,
    date_str: str,
    min_overlap_minutes: float = 5.0,
) -> list[dict]:
    """
    If a session overlaps a planner event window by >= min_overlap_minutes,
    mark it as meeting + attach meeting_match (planner_time_match).
    """
    events = get_calls_planner_events(calls_list_id, date_str)

    if not events:
        return sessions

    enriched: list[dict] = []
    for sess in sessions:
        s = dict(sess)

        ss_ms, se_ms = _session_ms_window(s)
        if ss_ms is None or se_ms is None:
            enriched.append(s)
            continue

        best: PlannerEvent | None = None
        best_ov = 0.0
        for ev in events:
            ov = _overlap_minutes(ss_ms, se_ms, ev.start_ms, ev.end_ms)
            if ov > best_ov:
                best_ov = ov
                best = ev

        if best and best_ov >= min_overlap_minutes:
            s["zone"] = "meeting"
            s["clickup_task_id"] = best.task_id
            s["clickup_task_name"] = best.task_name
            s["meeting_match"] = {
                "matched": True,
                "task_id": best.task_id,
                "task_name": best.task_name,
                "confidence": 0.95,
                "match_source": "planner_time_match",
                "overlap_minutes": round(best_ov, 1),
                "parent_name": best.parent_name,
            }
        enriched.append(s)

    return enriched
