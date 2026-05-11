"""
Supabase client for reading ActivityChunk, ActivityChunkEntry, DailyInputStats,
WorkSession, User, Task, and TaskSession tables.
Uses service role key to read all users' data.
"""

from __future__ import annotations

import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import config  # noqa: F401 — loads repo-root .env

from typing import Any

from supabase import Client, create_client


def get_client() -> Client:
    url = (os.getenv("SUPABASE_URL", "") or "").strip()
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or "").strip()
    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
    return create_client(url, key)


def _normalize_calendar_date(date_str: str) -> str:
    """Use YYYY-MM-DD only (workDate / date filters)."""
    s = (date_str or "").strip()
    return s[:10] if len(s) >= 10 else s


def _ist_day_start_end_ms(calendar_date: str) -> tuple[int, int]:
    """IST (UTC+5:30) calendar day bounds in epoch ms — matches worker get_date_str semantics."""
    d = _normalize_calendar_date(calendar_date)
    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    day_start = datetime.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=ist)
    start_ms = int(day_start.timestamp() * 1000)
    end_ms = start_ms + 86_400_000 - 1
    return start_ms, end_ms


def _session_calendar_date_str(rec: dict[str, Any]) -> str | None:
    """Best-effort calendar YYYY-MM-DD from a WorkSession row."""
    for k in ("workDate", "work_date"):
        v = rec.get(k)
        if v is None or v == "":
            continue
        s = str(v).strip()
        if len(s) >= 10:
            return s[:10]
    return None


def _session_matches_calendar_date(rec: dict[str, Any], d: str) -> bool:
    """True if this session belongs to calendar day d (IST), using workDate or loginAtMs."""
    wd = _session_calendar_date_str(rec)
    if wd == d:
        return True
    login = rec.get("loginAtMs")
    if login is None:
        login = rec.get("login_at_ms")
    if login is not None:
        try:
            sm, em = _ist_day_start_end_ms(d)
            li = int(login)
            if sm <= li <= em:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _fetch_work_sessions_broad(client: Client, user_id: str, d: str) -> list[dict[str, Any]]:
    """
    Load recent WorkSession rows for user and filter in Python by calendar day.
    Covers workDate type/format mismatches with strict .eq().
    """
    out: list[dict[str, Any]] = []
    for uc in ("userId", "user_id"):
        try:
            result = client.table("WorkSession").select("*").eq(uc, user_id).order("loginAtMs", desc=True).limit(500).execute()
            rows = result.data or []
        except Exception:
            try:
                result = client.table("WorkSession").select("*").eq(uc, user_id).order("login_at_ms", desc=True).limit(500).execute()
                rows = result.data or []
            except Exception:
                continue
        out = [r for r in rows if isinstance(r, dict) and _session_matches_calendar_date(r, d)]
        if out:
            return out
    return []


def fetch_all_users() -> list[dict[str, Any]]:
    """Returns all users from the User table."""
    client = get_client()
    result = client.table("User").select("id, email, clickupUsername, timezone").execute()
    return result.data or []


def _task_status_is_open(row: dict[str, Any]) -> bool:
    s = str(row.get("status") or row.get("taskStatus") or row.get("task_status") or "").strip().lower()
    return s in ("in progress", "open")


def _task_row_to_pipeline_task(row: dict[str, Any]) -> dict[str, Any]:
    """ClickUp-shaped dict for pipeline.mapping.task_mapper."""
    tid = row.get("clickupTaskId") or row.get("clickup_task_id") or row.get("id")
    out = dict(row)
    out["id"] = str(tid).strip() if tid is not None and str(tid).strip() else ""
    name = row.get("name") or row.get("title") or ""
    out["name"] = str(name).strip() or out["id"]
    desc = row.get("description") or row.get("body") or ""
    if desc:
        out["description"] = str(desc).strip()
    pid = row.get("parentId") or row.get("parent_id") or row.get("parentTaskId") or row.get("parent_task_id")
    if pid is not None and str(pid).strip():
        out["parent"] = {"id": str(pid).strip()}
    else:
        out.pop("parent", None)
    te_raw = row.get("timeEstimate") or row.get("time_estimate") or 0
    try:
        te_ms = int(te_raw)
    except (TypeError, ValueError):
        te_ms = 0
    out["time_estimate"] = te_ms // 60000
    out["due_date"] = row.get("dueDate") or row.get("due_date")
    out["status"] = row.get("status") or row.get("taskStatus") or row.get("task_status") or "in progress"
    out["url"] = row.get("url") or ""
    out["list_name"] = row.get("listName") or row.get("list_name") or ""
    return out


def fetch_tasks_for_user(user_id: str) -> list[dict[str, Any]]:
    """
    Read Task for this user; keep rows whose status is in ('in progress', 'open').
    Returns pipeline-ready dicts: id, name, optional description, optional parent {{ id }}.
    """
    client = get_client()
    rows: list[dict[str, Any]] = []
    for uid_col in ("userId", "user_id"):
        try:
            result = client.table("Task").select("*").eq(uid_col, user_id).execute()
            rows = result.data or []
            break
        except Exception:
            continue
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict) or not _task_status_is_open(r):
            continue
        if not (r.get("clickupTaskId") or r.get("clickup_task_id") or r.get("id")):
            continue
        out.append(_task_row_to_pipeline_task(r))
    return out


def _parse_epoch_ms(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    if s.isdigit():
        return int(s)
    try:
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def _task_session_start_end_ms(row: dict[str, Any]) -> tuple[int | None, int | None]:
    s = (
        _parse_epoch_ms(row.get("startMs"))
        or _parse_epoch_ms(row.get("start_ms"))
        or _parse_epoch_ms(row.get("startedAtMs"))
        or _parse_epoch_ms(row.get("started_at_ms"))
        or _parse_epoch_ms(row.get("start"))
    )
    e = (
        _parse_epoch_ms(row.get("endMs"))
        or _parse_epoch_ms(row.get("end_ms"))
        or _parse_epoch_ms(row.get("endedAtMs"))
        or _parse_epoch_ms(row.get("ended_at_ms"))
        or _parse_epoch_ms(row.get("end"))
    )
    return s, e


def _session_overlaps_ist_day(s_ms: int | None, e_ms: int | None, day_start: int, day_end: int) -> bool:
    if s_ms is None:
        return False
    if e_ms is None:
        return day_start <= s_ms <= day_end
    return e_ms >= day_start and s_ms <= day_end


def _task_session_row_to_time_entry(row: dict[str, Any]) -> dict[str, Any]:
    """Shape expected by task_mapper time-entry overlap (ms epoch)."""
    tid = row.get("taskId") or row.get("task_id")
    s_ms, e_ms = _task_session_start_end_ms(row)
    name = str(row.get("taskName") or row.get("task_name") or row.get("name") or "").strip()
    te: dict[str, Any] = {}
    if s_ms is not None:
        te["start"] = s_ms
    if e_ms is not None:
        te["end"] = e_ms
    if s_ms is not None and e_ms is not None and e_ms >= s_ms:
        te["duration"] = e_ms - s_ms
    if tid is not None:
        sid = str(tid).strip()
        te["task_id"] = sid
        te["task"] = {"id": sid, "name": name}
    return te


def fetch_task_sessions_for_user(user_id: str, date_str: str) -> list[dict[str, Any]]:
    """
    Read TaskSession for this user overlapping the IST calendar day of date_str.
    Returns time-entry-shaped dicts: start, end or duration (ms), task {{ id, name }}, task_id.
    """
    client = get_client()
    d = _normalize_calendar_date(date_str)
    day_start, day_end = _ist_day_start_end_ms(d)
    raw: list[dict[str, Any]] = []

    for uid_col in ("userId", "user_id"):
        for end_c, start_c in (("endMs", "startMs"), ("end_ms", "start_ms")):
            try:
                result = (
                    client.table("TaskSession")
                    .select("*")
                    .eq(uid_col, user_id)
                    .gte(end_c, day_start)
                    .lte(start_c, day_end)
                    .execute()
                )
                raw = result.data or []
                if raw:
                    return [_task_session_row_to_time_entry(r) for r in raw if isinstance(r, dict)]
            except Exception:
                continue

    for uid_col in ("userId", "user_id"):
        try:
            result = client.table("TaskSession").select("*").eq(uid_col, user_id).order("startMs", desc=True).limit(2000).execute()
            raw = result.data or []
        except Exception:
            try:
                result = client.table("TaskSession").select("*").eq(uid_col, user_id).order("start_ms", desc=True).limit(2000).execute()
                raw = result.data or []
            except Exception:
                raw = []
        if raw:
            break

    out: list[dict[str, Any]] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        s_ms, e_ms = _task_session_start_end_ms(r)
        if _session_overlaps_ist_day(s_ms, e_ms, day_start, day_end):
            out.append(_task_session_row_to_time_entry(r))
    return out


def _work_session_column_pairs() -> list[tuple[str, str]]:
    """(userId column, workDate column) — try camelCase first, then snake_case."""
    return [
        ("userId", "workDate"),
        ("user_id", "work_date"),
    ]


def fetch_recordings_for_user(user_id: str, date_str: str) -> list[dict[str, Any]]:
    """Returns WorkSession rows for a user on a given workDate."""
    client = get_client()
    d = _normalize_calendar_date(date_str)
    for uc, wc in _work_session_column_pairs():
        try:
            result = client.table("WorkSession").select("*").eq(uc, user_id).eq(wc, d).execute()
            rows = result.data or []
            if rows:
                return rows
        except Exception:
            continue
    try:
        uc, wc = _work_session_column_pairs()[0]
        result = client.table("WorkSession").select("*").eq(uc, user_id).eq(wc, d).execute()
        rows = result.data or []
        if rows:
            return rows
    except Exception:
        pass
    # workDate strict match can miss rows (timestamp vs date, TZ). Filter in Python.
    return _fetch_work_sessions_broad(client, user_id, d)


def _work_session_id_column_pairs() -> list[tuple[str, str]]:
    """(fk column on ActivityChunk, id column on WorkSession) for ordering/debug — only fk used in filter."""
    return [
        ("workSessionId", "id"),
        ("work_session_id", "id"),
    ]


def fetch_chunks_for_recording(recording_id: str) -> list[dict[str, Any]]:
    """Returns all ActivityChunk rows for a work session, ordered by seq (recording_id is workSessionId)."""
    client = get_client()
    for fk_col, _ in _work_session_id_column_pairs():
        try:
            result = (
                client.table("ActivityChunk")
                .select("*")
                .eq(fk_col, recording_id)
                .order("seq")
                .execute()
            )
            rows = result.data or []
            if rows:
                return rows
        except Exception:
            continue
    try:
        fk_col, _ = _work_session_id_column_pairs()[0]
        result = (
            client.table("ActivityChunk")
            .select("*")
            .eq(fk_col, recording_id)
            .order("seq")
            .execute()
        )
        return result.data or []
    except Exception:
        return []


def _chunk_entry_chunk_id_columns() -> list[str]:
    return ["chunkId", "chunk_id"]


def fetch_chunk_entries(chunk_id: str) -> list[dict[str, Any]]:
    """Returns ActivityChunkEntry rows for a chunk."""
    client = get_client()
    for col in _chunk_entry_chunk_id_columns():
        try:
            result = client.table("ActivityChunkEntry").select("*").eq(col, chunk_id).execute()
            rows = result.data or []
            if rows:
                return rows
        except Exception:
            continue
    return []


def _chunks_for_user_by_start_ms(client: Client, user_id: str, calendar_date: str) -> list[dict[str, Any]]:
    """Fallback: ActivityChunk.userId + overlap with IST calendar day (no WorkSession join)."""
    start_ms, end_ms = _ist_day_start_end_ms(calendar_date)
    for user_col in ("userId", "user_id"):
        for end_col, start_col in (("endMs", "startMs"), ("end_ms", "start_ms")):
            try:
                result = (
                    client.table("ActivityChunk")
                    .select("*")
                    .eq(user_col, user_id)
                    .gte(end_col, start_ms)
                    .lte(start_col, end_ms)
                    .order("seq")
                    .execute()
                )
                rows = result.data or []
                if rows:
                    return rows
            except Exception:
                continue
    return []


def _dedupe_chunks_merge(
    session_chunks: list[dict[str, Any]], overlap_chunks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Union by chunk id; session-linked rows first so FK path wins on duplicate."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def _take(c: dict[str, Any]) -> None:
        cid = c.get("id")
        if cid is None:
            out.append(dict(c))
            return
        s = str(cid)
        if s in seen:
            return
        seen.add(s)
        out.append(dict(c))

    for c in session_chunks:
        _take(c)
    for c in overlap_chunks:
        _take(c)
    return out


def _chunk_sort_key(c: dict[str, Any]) -> tuple[int, int]:
    start = c.get("startMs")
    if start is None:
        start = c.get("start_ms")
    try:
        sm = int(start) if start is not None else 0
    except (TypeError, ValueError):
        sm = 0
    seq = c.get("seq", 0)
    try:
        sq = int(seq) if seq is not None else 0
    except (TypeError, ValueError):
        sq = 0
    return sm, sq


def _attach_entries(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ch in chunks:
        ch = dict(ch)
        cid = ch.get("id")
        if cid is not None:
            ch["entries"] = fetch_chunk_entries(str(cid))
        else:
            ch["entries"] = []
        out.append(ch)
    return out


def fetch_chunks_for_user_date(user_id: str, date_str: str) -> list[dict[str, Any]]:
    """
    All ActivityChunk rows for this user that overlap the IST calendar day.

    Loads chunks linked from WorkSession rows for that day, then merges with
    ActivityChunk filtered by userId + time overlap. Without the merge, chunks
    whose workSessionId points at a session missing from the day query (or null FK)
    would be dropped even when 68 rows exist in the table for that user/day.
    """
    client = get_client()
    d = _normalize_calendar_date(date_str)

    sessions = fetch_recordings_for_user(user_id, d)
    from_sessions: list[dict[str, Any]] = []
    for rec in sessions:
        sid = rec.get("id")
        if not sid:
            continue
        from_sessions.extend(fetch_chunks_for_recording(str(sid)))

    overlap = _chunks_for_user_by_start_ms(client, user_id, d)
    merged = _dedupe_chunks_merge(from_sessions, overlap)
    merged.sort(key=_chunk_sort_key)
    return _attach_entries(merged)


def fetch_input_activity(user_id: str, date_str: str) -> dict[str, Any] | None:
    """Returns aggregated DailyInputStats for a user on a date."""
    client = get_client()
    d = _normalize_calendar_date(date_str)
    for date_col, uid_col in (("date", "userId"), ("date", "user_id")):
        try:
            result = client.table("DailyInputStats").select("*").eq(uid_col, user_id).eq(date_col, d).execute()
            rows = result.data or []
            if rows:
                total_clicks = sum(r.get("clicks", 0) or 0 for r in rows)
                total_presses = sum(r.get("presses", 0) or 0 for r in rows)
                return {
                    "userId": user_id,
                    "day": d,
                    "clicks": total_clicks,
                    "presses": total_presses,
                }
        except Exception:
            continue
    return None
