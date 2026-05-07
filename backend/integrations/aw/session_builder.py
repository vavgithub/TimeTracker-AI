from collections import defaultdict
from datetime import datetime, timedelta
import sys

from config import (
    APPLY_AFK_FILTER,
    IGNORE_APPS,
    MAX_SESSION_MINUTES,
    MERGE_GAP_MINUTES,
    MIN_DURATION,
    SESSION_GAP_MINUTES,
)
from utils.helpers import get_domain


def normalize(events, source):
    normalized = []
    for e in events:
        start = datetime.fromisoformat(e["timestamp"].replace("Z", ""))
        end = start + timedelta(seconds=e["duration"])
        normalized.append(
            {
                "start": start,
                "end": end,
                "duration": e["duration"],
                "source": source,
                "data": e["data"],
            }
        )
    return normalized


def should_split(prev, curr):
    gap = timedelta(minutes=SESSION_GAP_MINUTES)
    if curr["start"] - prev["end"] > gap:
        return True

    prev_app = prev["data"].get("app", "")
    curr_app = curr["data"].get("app", "")

    work_apps = ["Cursor", "Code", "Terminal"]
    browser_apps = ["Brave", "Chrome"]
    meeting_apps = ["zoom"]

    def app_type(app):
        if app in work_apps:
            return "work"
        if app in browser_apps:
            return "browser"
        if app in meeting_apps:
            return "meeting"
        return "other"

    if app_type(prev_app) != app_type(curr_app):
        return True

    prev_url = prev["data"].get("url")
    curr_url = curr["data"].get("url")
    prev_domain = get_domain(prev_url) if prev_url else None
    curr_domain = get_domain(curr_url) if curr_url else None
    if prev_domain and curr_domain:
        if ("youtube" in curr_domain or "instagram" in curr_domain) or (
            "youtube" in prev_domain and curr_domain != prev_domain
        ):
            return True

    return False


def _build_sessions_from_events(events):
    sessions = []
    current_session = []

    for event in events:
        if not current_session:
            current_session.append(event)
            continue

        last_event = current_session[-1]

        if should_split(last_event, event):
            sessions.append(current_session)
            current_session = [event]
        else:
            current_session.append(event)

    if current_session:
        sessions.append(current_session)

    return sessions


def merge_sessions(sessions):
    merge_gap = timedelta(minutes=MERGE_GAP_MINUTES)
    if not sessions:
        return []

    merged = []
    current = sessions[0]

    for s in sessions[1:]:
        gap = s[0]["start"] - current[-1]["end"]
        if gap <= merge_gap:
            current.extend(s)
        else:
            merged.append(current)
            current = s

    merged.append(current)
    return merged


def split_large_sessions(sessions):
    new_sessions = []
    max_min = MAX_SESSION_MINUTES

    for s in sessions:
        start = s[0]["start"]
        end = s[-1]["end"]
        duration = (end - start).total_seconds() / 60.0

        if duration <= max_min:
            new_sessions.append(s)
            continue

        chunk = []
        chunk_start = s[0]["start"]

        for e in s:
            if (e["start"] - chunk_start).seconds / 60 > max_min:
                if chunk:
                    new_sessions.append(chunk)
                chunk = [e]
                chunk_start = e["start"]
            else:
                chunk.append(e)

        if chunk:
            new_sessions.append(chunk)

    return new_sessions


def is_active(session):
    for e in session:
        if e["source"] == "afk":
            status = str(e["data"].get("status", "")).lower()
            if "not" in status:
                return True
    return True


def summarize(session):
    urls = []
    apps = set()
    titles = []

    for e in session:
        if e["source"] == "web":
            url = e["data"].get("url")
            if url:
                urls.append(url)
            title = e["data"].get("title")
            if title:
                titles.append(title)
        elif e["source"] == "window":
            app = e["data"].get("app")
            if app:
                apps.add(app)
            title = e["data"].get("title")
            if title:
                titles.append(title)

    if not urls:
        app_context = ", ".join(sorted(apps)) if apps else "NO_APP_CONTEXT"
        urls = [f"APP_CONTEXT: {app_context}"]

    start = session[0]["start"]
    end = session[-1]["end"]

    return {
        "start": start,
        "end": end,
        "duration_min": (end - start).total_seconds() / 60.0,
        "urls": list(set(urls))[:5],
        "apps": list(apps),
        "titles": titles[:12],
    }


def _session_bounds_ts(session: dict) -> tuple[float, float]:
    start = session.get("start")
    end = session.get("end")
    if isinstance(start, datetime) and isinstance(end, datetime):
        return start.timestamp(), end.timestamp()
    s0 = str(start).replace("Z", "+00:00")
    s1 = str(end).replace("Z", "+00:00")
    d0 = datetime.fromisoformat(s0)
    d1 = datetime.fromisoformat(s1)
    # If timestamps are naive, assume UTC (AW commonly emits UTC-ish strings).
    if d0.tzinfo is None:
        d0 = d0.replace(tzinfo=timezone.utc)
    if d1.tzinfo is None:
        d1 = d1.replace(tzinfo=timezone.utc)
    return (
        d0.timestamp(),
        d1.timestamp(),
    )


def _event_bounds_ts(e: dict) -> tuple[float, float]:
    ts = str(e.get("timestamp", "")).replace("Z", "+00:00")
    d0 = datetime.fromisoformat(ts)
    if d0.tzinfo is None:
        d0 = d0.replace(tzinfo=timezone.utc)
    t0 = d0.timestamp()
    t1 = t0 + float(e.get("duration") or 0)
    return t0, t1


def build_app_breakdown(session: dict, window_events: list, web_events: list) -> list[dict]:
    """
    Per-app, per-tab seconds within the session window (window watcher),
    URLs from web watcher by title, with browser/editor title cleanup.
    """
    s_start, s_end = _session_bounds_ts(session)

    NOISE_TITLES = frozenset({"loading", "unknown", "", "new tab", "untitled"})
    BROWSER_SUBSTRS = frozenset(
        ("brave", "google-chrome", "chromium", "firefox", "chrome")
    )
    EDITOR_SUBSTRS = frozenset(("cursor", "code", "vscode"))

    app_title_time: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for e in window_events or []:
        if not isinstance(e, dict):
            continue
        data = e.get("data") or {}
        try:
            e_start, e_end = _event_bounds_ts(e)
        except (TypeError, ValueError, KeyError):
            continue
        overlap = max(0.0, min(s_end, e_end) - max(s_start, e_start))
        if overlap < 2.0:
            continue
        app = data.get("app") or "unknown"
        title = (data.get("title") or "").strip()
        tl = title.lower()
        if tl in NOISE_TITLES or len(title) < 3:
            continue
        app_title_time[app][title] += overlap

    url_map: dict[str, str] = {}
    for e in web_events or []:
        if not isinstance(e, dict):
            continue
        data = e.get("data") or {}
        try:
            e_start, e_end = _event_bounds_ts(e)
        except (TypeError, ValueError, KeyError):
            continue
        overlap = max(0.0, min(s_end, e_end) - max(s_start, e_start))
        if overlap < 1.0:
            continue
        title = (data.get("title") or "").strip()
        url = (data.get("url") or "").strip()
        if title and url and "APP_CONTEXT" not in url:
            url_map[title] = url

    breakdown: list[dict] = []
    for app, title_times in sorted(
        app_title_time.items(),
        key=lambda x: sum(x[1].values()),
        reverse=True,
    ):
        app_lower = (app or "").lower()
        is_browser = any(b in app_lower for b in BROWSER_SUBSTRS)
        is_editor = any(ed in app_lower for ed in EDITOR_SUBSTRS)

        tabs: list[dict] = []
        for title, secs in sorted(title_times.items(), key=lambda x: x[1], reverse=True):
            clean_title = title
            clean_project = ""
            if is_editor and " - " in title:
                parts = [p.strip() for p in title.split(" - ")]
                clean_title = parts[0] if parts else title
                clean_project = parts[1] if len(parts) > 1 else ""

            if is_browser:
                for suffix in (
                    " - Brave",
                    " - Google Chrome",
                    " - Firefox",
                    " - Chromium",
                ):
                    if clean_title.endswith(suffix):
                        clean_title = clean_title[: -len(suffix)]
                        break

            url = url_map.get(title, "") or url_map.get(clean_title, "")

            if "localhost:8080" in url and "code=" in url:
                clean_title = "Google OAuth callback"
                url = ""

            tabs.append(
                {
                    "title": clean_title,
                    "project": clean_project,
                    "url": url,
                    # Keep "minutes" (existing consumers) and also expose "duration_minutes"
                    # for clarity in prompts and UI.
                    "minutes": round(secs / 60.0, 1),
                    "duration_minutes": round(secs / 60.0, 1),
                }
            )

        total = sum(title_times.values())
        breakdown.append(
            {
                "app": app,
                "total_minutes": round(total / 60.0, 1),
                "is_browser": is_browser,
                "is_editor": is_editor,
                "tabs": tabs,
            }
        )

    return breakdown


def split_sessions_at_meeting_boundaries(
    sessions: list[dict],
    calendar_events: list[dict],
    window_events: list[dict] | None = None,
) -> list[dict]:
    """
    If a calendar event ends inside a session (regardless of when it started),
    split that session at the event boundary:
      Part 1: session.start → event.end  (meeting portion — keeps zone/meeting_match)
      Part 2: event.end → session.end    (work after meeting — meeting markers cleared)

    The event may have started before the session (user joined late) — only its
    end time matters for the split point.
    Only splits when the work portion is >= MIN_DURATION seconds.
    """
    result = []
    for session in sessions:
        s_start = session["start"]
        s_end = session["end"]

        if isinstance(s_start, datetime):
            s_start_ts = s_start.timestamp()
        else:
            s_start_ts = datetime.fromisoformat(str(s_start).replace("Z", "+00:00")).timestamp()

        if isinstance(s_end, datetime):
            s_end_ts = s_end.timestamp()
        else:
            s_end_ts = datetime.fromisoformat(str(s_end).replace("Z", "+00:00")).timestamp()

        split_done = False
        for event in calendar_events:
            e_start_ts = event["start_ts"]
            e_end_ts = event["end_ts"]

            actual_end_ts = float(e_end_ts)
            # If Zoom/Meet is still active after the calendar end, extend the meeting
            # portion to the last "Meeting" window event end (capped to +90 minutes).
            if window_events:
                last_zoom_end = None
                for we in window_events:
                    if not isinstance(we, dict):
                        continue
                    data = we.get("data") or {}
                    app = str(data.get("app") or "").strip().lower()
                    title = str(data.get("title") or "").strip()
                    if app != "zoom":
                        continue
                    if title != "Meeting":
                        continue
                    try:
                        we_start, we_end = _event_bounds_ts(we)
                    except (TypeError, ValueError, KeyError):
                        continue
                    overlap = max(0.0, min(s_end_ts, we_end) - max(s_start_ts, we_start))
                    if overlap <= 0:
                        continue
                    if last_zoom_end is None or we_end > last_zoom_end:
                        last_zoom_end = we_end

                if last_zoom_end and last_zoom_end > actual_end_ts:
                    diff_min = (last_zoom_end - actual_end_ts) / 60.0
                    if diff_min <= 90.0:
                        actual_end_ts = float(last_zoom_end)

            # Event end must fall inside the session (overlaps session and ends
            # before session ends), with a work tail of at least MIN_DURATION seconds.
            # The event start can be before the session (user joined late).
            if (
                actual_end_ts > s_start_ts
                and actual_end_ts < s_end_ts
                and (s_end_ts - actual_end_ts) >= MIN_DURATION
            ):
                # Derive split-point datetime as an offset from the session's own start,
                # so it inherits the same tzinfo (naive or aware) and stays comparable.
                e_end_dt = s_start + timedelta(seconds=(actual_end_ts - s_start_ts))

                meeting_part = dict(session)
                meeting_part["end"] = e_end_dt
                meeting_part["duration_min"] = (actual_end_ts - s_start_ts) / 60.0

                work_part = dict(session)
                work_part["start"] = e_end_dt
                work_part["duration_min"] = (s_end_ts - actual_end_ts) / 60.0
                # Strip meeting classification from the work portion.
                work_part.pop("zone", None)
                work_part.pop("meeting_match", None)
                work_part.pop("clickup_task_id", None)
                work_part.pop("clickup_task_name", None)

                result.append(meeting_part)
                result.append(work_part)
                split_done = True
                break

        if not split_done:
            result.append(session)

    return result


def build_sessions(web, window, afk):
    """
    Merge web (from all buckets), window, and AFK events into sessions,
    then return a list of session summary dicts for classification.
    """
    events = (
        normalize(web, "web")
        + normalize(window, "window")
        + normalize(afk, "afk")
    )
    events.sort(key=lambda x: x["start"])
    events = [e for e in events if e["data"].get("app") not in IGNORE_APPS]

    sessions = _build_sessions_from_events(events)
    sessions = [s for s in sessions if sum(e["duration"] for e in s) >= MIN_DURATION]
    sessions = merge_sessions(sessions)
    sessions = split_large_sessions(sessions)

    if APPLY_AFK_FILTER:
        sessions = [s for s in sessions if is_active(s)]

    summarized = [summarize(s) for s in sessions]
    # Filter clearly-AFK/overnight sessions that leak across midnight when AFK isn't detected.
    # These pollute daily totals and EOD summaries.
    filtered = []
    removed = 0
    for s in summarized:
        try:
            dur_min = float(s.get("duration_min") or 0.0)
        except Exception:
            dur_min = 0.0
        if dur_min > 480:
            removed += 1
            print(
                f"[WARN] Dropping overnight/AFK session: {dur_min:.0f} min "
                f"{str(s.get('start'))[:19]} → {str(s.get('end'))[:19]}",
                file=sys.stderr,
            )
            continue
        filtered.append(s)

    if removed:
        print(f"[WARN] Dropped {removed} session(s) > 480 minutes.", file=sys.stderr)

    return filtered
