"""
Converts Supabase WorkActivityChunk summaries into the same
event + session format that the existing pipeline expects.

Each chunk covers ~2 minutes. summary contains:
  apps: [{ app, durationMs }]
  titles: [{ app, title, durationMs }]
  domains: [{ domain, durationMs, visitCount }]
  activeMs: int
  afkMs: int
  date: ISO string
"""

from __future__ import annotations

import datetime
from typing import Any

MS_TO_SEC = 1 / 1000


def _ms_to_iso(ms: int) -> str:
    dt = datetime.datetime.utcfromtimestamp(ms / 1000)
    return dt.isoformat()


def chunks_to_sessions(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Convert Supabase chunks to a list of session dicts compatible
    with what session_builder.py produces.

    Each chunk becomes one session. Adjacent chunks with the same
    dominant app are merged if gap < 5 minutes.
    """
    if not chunks:
        return []

    sessions = []
    for chunk in chunks:
        start_ms = chunk.get("startMs", 0)
        end_ms = chunk.get("endMs", 0)
        summary = chunk.get("summary") or {}

        apps_raw = summary.get("apps") or []
        titles_raw = summary.get("titles") or []
        domains_raw = summary.get("domains") or []
        active_ms = summary.get("activeMs", 0) or 0
        afk_ms = summary.get("afkMs", 0) or 0

        # Extract unique app names sorted by duration
        apps = [a["app"] for a in sorted(apps_raw, key=lambda x: x.get("durationMs", 0), reverse=True) if a.get("app")]
        # Remove browser process names, keep meaningful ones
        apps = [a for a in apps if a.lower() not in ("chrome.exe", "electron.exe", "brave.exe")]
        # Add browser back as "Chrome" / "Brave" if it was dominant
        for a in apps_raw:
            raw = (a.get("app") or "").lower()
            if "chrome" in raw and "Chrome" not in apps:
                apps.insert(0, "Chrome")
            elif "brave" in raw and "Brave" not in apps:
                apps.insert(0, "Brave")

        # Extract window titles
        titles = list(dict.fromkeys([
            t["title"] for t in sorted(titles_raw, key=lambda x: x.get("durationMs", 0), reverse=True)
            if t.get("title")
        ]))

        # Extract URLs from domains
        urls = [d["domain"] for d in domains_raw if d.get("domain")]

        duration_sec = (end_ms - start_ms) * MS_TO_SEC
        duration_min = duration_sec / 60
        activity_rate = round((active_ms / max(active_ms + afk_ms, 1)) * 100, 1) if (active_ms + afk_ms) > 0 else 0.0

        session = {
            "session_id": f"sb_{chunk.get('id', '')}_{start_ms}",
            "start": _ms_to_iso(start_ms),
            "end": _ms_to_iso(end_ms),
            "duration_min": round(duration_min, 2),
            "duration_hours": round(duration_min / 60, 4),
            "apps": apps,
            "titles": titles,
            "urls": urls,
            "zone": "unclear",
            "activity_rate": activity_rate,
            "active_ms": active_ms,
            "afk_ms": afk_ms,
            "input": {
                "keystrokes": 0,
                "mouse_clicks": 0,
                "activity_rate": activity_rate,
                "scroll_units": 0,
            },
            "app_breakdown": [
                {
                    "app": a.get("app", ""),
                    "minutes": round(a.get("durationMs", 0) / 60000, 1),
                    "titles": [t["title"] for t in titles_raw if t.get("app") == a.get("app")]
                }
                for a in apps_raw if a.get("app")
            ],
            "ai_enrichment": {},
        }
        sessions.append(session)

    # Merge adjacent sessions with same dominant app if gap < 5 min
    merged = _merge_adjacent_sessions(sessions)
    return merged


def _merge_adjacent_sessions(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge consecutive sessions if gap < 5 minutes."""
    if not sessions:
        return sessions

    import datetime
    result = [sessions[0]]
    for curr in sessions[1:]:
        prev = result[-1]
        try:
            prev_end = datetime.datetime.fromisoformat(prev["end"])
            curr_start = datetime.datetime.fromisoformat(curr["start"])
            gap_min = (curr_start - prev_end).total_seconds() / 60
        except Exception:
            result.append(curr)
            continue

        if gap_min < 5 and _dominant_app(prev) == _dominant_app(curr):
            # Merge into prev
            prev["end"] = curr["end"]
            prev["duration_min"] = round(prev["duration_min"] + curr["duration_min"], 2)
            prev["apps"] = list(dict.fromkeys(prev["apps"] + curr["apps"]))
            prev["titles"] = list(dict.fromkeys(prev["titles"] + curr["titles"]))
            prev["urls"] = list(dict.fromkeys(prev["urls"] + curr["urls"]))
            prev["active_ms"] = prev.get("active_ms", 0) + curr.get("active_ms", 0)
        else:
            result.append(curr)

    return result


def _dominant_app(session: dict[str, Any]) -> str:
    apps = session.get("apps") or []
    return apps[0] if apps else ""


def input_activity_to_daily(input_day: dict[str, Any] | None) -> dict[str, Any]:
    """Convert InputActivityDay to productivity metrics format."""
    if not input_day:
        return {"keystrokes": 0, "mouse_clicks": 0}
    return {
        "keystrokes": input_day.get("presses", 0) or 0,
        "mouse_clicks": input_day.get("clicks", 0) or 0,
    }

