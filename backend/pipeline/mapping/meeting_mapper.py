"""
Map Zoom/Meet/Teams sessions to ClickUp Calls list tasks (standup, workshop, …).

Multitasking during standup (Cursor/Chrome while Zoom is open) used to falsely match
"Portfolio review" / "Project review call" because single tokens like "portfolio" or
URL segments like "project" appeared in unrelated tabs. Matching is now:
  titles-first phrases → full task name in titles → ambiguous Zoom → Standup default.
"""
from __future__ import annotations

import os
import re
import config  # noqa: F401 — loads repo-root `.env`

from integrations.clickup.client import ClickUpClient, flatten_calls_list_tasks

_MEETING_PHRASES: list[tuple[str, str]] = [
    ("project review call", "Project review call"),
    ("project review", "Project review call"),
    ("portfolio review", "Portfolio review"),
    ("stand-up", "Standup call"),
    ("stand up", "Standup call"),
    ("standup", "Standup call"),
    ("daily standup", "Standup call"),
    ("workshop", "Workshop Call"),
    ("client call", "Client Call"),
    ("syncup", "Syncups"),
    ("sync-ups", "Syncups"),
    ("retro", "Retro call"),
    ("hiring", "Hiring Call"),
    ("illustrator", "Illustrator Workshop"),
    ("self review", "Self review"),
    ("all hands", "Miscellaneous"),
]

_EXPLICIT_OTHER_MEETING = (
    "workshop",
    "client call",
    "project review",
    "portfolio review",
    "syncup",
    "retro",
    "hiring",
    "illustrator",
    "interview",
    "onboarding",
    "training",
    "webinar",
)


def get_meeting_tasks(calls_list_id: str) -> list[dict]:
    """Pull Calls list tasks with subtasks; flatten to matchable rows {id, name, parent_name}."""
    cu = ClickUpClient()
    if not cu.token or not calls_list_id:
        return []
    raw = cu.get_list_tasks(calls_list_id)
    return flatten_calls_list_tasks(raw)


def _titles_blob(session: dict) -> str:
    return " ".join(str(t) for t in (session.get("titles") or [])).lower()


def _primary_coding_work(session: dict) -> bool:
    """
    IDE / file work is the dominant activity (Zoom may be open in the background).
    """
    apps = " ".join(session.get("apps") or []).lower()
    if not any(
        x in apps
        for x in (
            "cursor",
            "code",
            "nvim",
            "pycharm",
            "webstorm",
            "intellij",
            "goland",
            "sublime",
        )
    ):
        return False
    tb = _titles_blob(session)
    if re.search(r"\.(py|ts|tsx|js|jsx|go|rs|java|cs|cpp|h|vue|svelte)\b", tb, re.I):
        return True
    if " - cursor" in tb or "visual studio code" in tb or "vscode" in tb:
        return True
    return False


def _strong_meeting_title_signal(session: dict) -> bool:
    """Titles that clearly indicate a real meeting, not generic 'Meeting' while coding."""
    t = _titles_blob(session)
    for phrase, _canon in _MEETING_PHRASES:
        if phrase in t:
            return True
    if any(
        x in t
        for x in (
            "join from zoom",
            "meet.google",
            "teams.microsoft",
            "zoom meeting",
            "google meet",
        )
    ):
        return True
    return False


def _has_zoom_or_teams(session: dict) -> bool:
    apps = " ".join(session.get("apps") or []).lower()
    if any(x in apps for x in ("zoom", "teams", "meet", "webex")):
        return True
    blob = _titles_blob(session)
    urls = " ".join(str(u) for u in (session.get("urls") or [])).lower()
    combined = blob + " " + urls
    return any(x in combined for x in ("zoom", "meet.google", "teams.microsoft", "webex"))


def is_meeting_session(session: dict) -> bool:
    """Detect meeting-like activity from apps, titles, and URLs."""
    # Coding override: Zoom/Teams open but user is primarily in IDE with file tabs — not a meeting block.
    if _primary_coding_work(session) and not _strong_meeting_title_signal(session):
        return False

    if _has_zoom_or_teams(session):
        return True

    meeting_signals = [
        "meet.google",
        "teams.microsoft",
        "webex",
        "standup",
        "workshop",
        "syncup",
        "stand-up",
    ]
    titles = _titles_blob(session)
    urls = " ".join(str(u) for u in (session.get("urls") or [])).lower()
    combined = titles + " " + urls
    if any(signal in combined for signal in meeting_signals):
        return True
    if "meeting" in titles and ("join" in titles or "zoom" in titles):
        return True
    return False


def _task_by_name_lower(meeting_tasks: list[dict]) -> dict[str, dict]:
    return {(t.get("name") or "").strip().lower(): t for t in meeting_tasks if t.get("name")}


def _titles_hint_other_meeting(titles: str) -> bool:
    return any(h in titles for h in _EXPLICIT_OTHER_MEETING)


def match_meeting_type(session: dict, meeting_tasks: list[dict]) -> dict:
    """
    Match session to a Calls list task/subtask. Uses titles only for fuzzy signals
    so unrelated browser tabs don't steal the meeting type.
    """
    titles = _titles_blob(session)
    by_name = _task_by_name_lower(meeting_tasks)

    for phrase, canonical in sorted(_MEETING_PHRASES, key=lambda x: -len(x[0])):
        if phrase in titles:
            task = by_name.get(canonical.lower())
            return {
                "matched": True,
                "task_id": task.get("id") if task else None,
                "task_name": canonical,
                "confidence": 0.88,
                "match_source": "title_phrase",
            }

    for task in sorted(meeting_tasks, key=lambda t: -len(t.get("name") or "")):
        name = (task.get("name") or "").strip()
        if len(name) < 5:
            continue
        if name.lower() in titles:
            return {
                "matched": True,
                "task_id": task.get("id"),
                "task_name": name,
                "confidence": 0.86,
                "match_source": "task_name_in_title",
            }

    if _has_zoom_or_teams(session) and not _titles_hint_other_meeting(titles):
        if "meeting" in titles or "join from zoom" in titles or "zoom workplace" in titles:
            st = by_name.get("standup call")
            if st:
                return {
                    "matched": True,
                    "task_id": st.get("id"),
                    "task_name": st.get("name"),
                    "confidence": 0.62,
                    "match_source": "standup_default_multitask",
                }

    return {
        "matched": True,
        "task_id": None,
        "task_name": "Meeting",
        "confidence": 0.50,
        "match_source": "zoom_detected_only",
    }


def process_meeting_sessions(sessions: list[dict], calls_list_id: str | None, *, debug: bool = False) -> list[dict]:
    """
    Tag meeting sessions with zone=meeting and meeting_match; pass others through unchanged.
    """
    calls_list_id = (calls_list_id or os.getenv("CLICKUP_CALLS_LIST_ID") or "").strip()
    if not calls_list_id:
        return sessions

    meeting_tasks = get_meeting_tasks(calls_list_id)

    enriched = []
    for session in sessions:
        s = dict(session)
        mm_src = (s.get("meeting_match") or {}).get("match_source")
        if s.get("zone") == "meeting" and mm_src in (
            "planner_time_match",
            "google_calendar",
            "google_calendar_zoom_extended",
            "zoom_late_start_workshop",
            "zoom_title_workshop_extended",
            "zoom_workshop_day_heuristic",
        ):
            enriched.append(s)
            continue

        if is_meeting_session(s):
            match = match_meeting_type(s, meeting_tasks)
            s["zone"] = "meeting"
            s["meeting_match"] = match
            s["clickup_task_id"] = match.get("task_id")
            s["clickup_task_name"] = match.get("task_name")
            try:
                dur = float(s.get("duration_min") or 0)
            except (TypeError, ValueError):
                dur = 0.0
            map_m = str(s.get("map_method") or "").lower()
            # Cap only meeting_mapper detections; never strip Google Calendar meetings.
            if dur > 60.0 and "calendar" not in map_m:
                s["zone"] = "unclear"
                s["map_method"] = "none"
                s.pop("meeting_match", None)
                prev = str(s.get("map_notes") or "").strip()
                note = "meeting_reverted: duration exceeds 60min cap"
                s["map_notes"] = f"{prev} — {note}" if prev else note
                s["clickup_task_id"] = None
                s["clickup_task_name"] = None
            if debug:
                if s.get("zone") == "meeting":
                    print(
                        f"  Meeting → {match.get('task_name')} "
                        f"(conf {match.get('confidence')}) "
                        f"[{s.get('duration_min', 0):.0f}m] "
                        f"[{match.get('match_source')}]"
                    )
                else:
                    print(
                        f"  Meeting reverted (>60m cap): was {match.get('task_name')} "
                        f"[{dur:.0f}m] [{match.get('match_source')}]"
                    )
        enriched.append(s)

    return enriched


if __name__ == "__main__":
    CALLS_LIST_ID = os.getenv("CLICKUP_CALLS_LIST_ID", "901612443044")

    test_sessions = [
        {
            "duration_min": 23.0,
            "titles": ["Meeting", "Standup call - Zoom", "Meeting"],
            "urls": [],
            "apps": ["zoom"],
        },
        {
            "duration_min": 34.5,
            "titles": ["session_builder.py - time_tracker - Cursor"],
            "urls": [],
            "apps": ["Cursor"],
        },
    ]

    out = process_meeting_sessions(test_sessions, CALLS_LIST_ID)
    print("\n=== SESSION ZONES ===")
    for s in out:
        zone = s.get("zone", "—")
        task = s.get("clickup_task_name") or ""
        mins = s.get("duration_min", 0)
        print(f"  {zone:<15} {task:<35} {mins:.0f}m")
