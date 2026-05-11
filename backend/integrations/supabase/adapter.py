"""
Converts Supabase ActivityChunk + ActivityChunkEntry rows into the same
event + session format that the existing pipeline expects.
"""

from __future__ import annotations

import datetime
from urllib.parse import urlparse
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
        active_ms = chunk.get("activeMs", 0) or 0
        afk_ms = chunk.get("afkMs", 0) or 0
        entries = chunk.get("entries", [])

        window_entries = [e for e in entries if e.get("kind") == "window"]
        browser_entries = [e for e in entries if e.get("kind") == "browser"]

        # apps from window entries
        app_durations: dict[str, int] = {}
        for e in window_entries:
            app = e.get("app") or ""
            if app:
                app_durations[app] = app_durations.get(app, 0) + (e.get("durationMs") or 0)

        # titles from window entries
        titles = list(
            dict.fromkeys(
                [
                    e["title"]
                    for e in sorted(window_entries, key=lambda x: x.get("durationMs", 0), reverse=True)
                    if e.get("title")
                ]
            )
        )

        # domains from browser entries
        domain_durations: dict[str, int] = {}
        for e in browser_entries:
            url = e.get("url") or ""
            if url:
                try:
                    domain = urlparse(url).netloc
                    if domain:
                        domain_durations[domain] = domain_durations.get(domain, 0) + (e.get("durationMs") or 0)
                except Exception:
                    pass

        urls = list(domain_durations.keys())

        # apps list sorted by duration
        apps_sorted = sorted(app_durations.items(), key=lambda x: x[1], reverse=True)
        apps = [a[0] for a in apps_sorted if a[0].lower() not in ("", "unknown")]

        # app_breakdown
        app_breakdown = [
            {
                "app": app,
                "minutes": round(dur / 60000, 1),
                "titles": [
                    e["title"] for e in window_entries if e.get("app") == app and e.get("title")
                ],
            }
            for app, dur in apps_sorted
        ]

        duration_sec = (end_ms - start_ms) * MS_TO_SEC
        duration_min = duration_sec / 60
        activity_rate = (
            round((active_ms / max(active_ms + afk_ms, 1)) * 100, 1) if (active_ms + afk_ms) > 0 else 0.0
        )

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
            "app_breakdown": app_breakdown,
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
    """Convert DailyInputStats aggregate dict to productivity metrics format."""
    if not input_day:
        return {"keystrokes": 0, "mouse_clicks": 0}
    return {
        "keystrokes": input_day.get("presses", 0) or 0,
        "mouse_clicks": input_day.get("clicks", 0) or 0,
    }
