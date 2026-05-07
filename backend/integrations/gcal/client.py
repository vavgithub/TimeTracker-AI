from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request

from config import REPO_ROOT, WORKSHOP_CALL_DAYS, ZOOM_EXTENDED_EVENTS
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Same calendar day window as the rest of time_tracker (IST).
IST = timezone(timedelta(hours=5, minutes=30))

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
CREDS_FILE = REPO_ROOT / "Credentials.json"
TOKEN_FILE = REPO_ROOT / "calendar_token.json"


def _oauth_port() -> int:
    try:
        return int(os.getenv("GCAL_OAUTH_PORT", "8080"))
    except ValueError:
        return 8080


def _print_oauth_troubleshooting(port: int) -> None:
    print(
        "\n[gcal] OAuth tips if authorization fails:\n"
        f"  • redirect_uri_mismatch: In Google Cloud Console → Credentials → your OAuth client, add\n"
        f"      http://localhost:{port}/\n"
        "    under **Authorized redirect URIs** (exact match, trailing slash as shown).\n"
        "  • Easier: create credentials type **Desktop app** and replace Credentials.json with that\n"
        "    download (loopback does not need a URI list).\n"
        "  • Error 403 access_denied / 'has not completed the Google verification process': the app is in\n"
        "    **Testing** on the OAuth consent screen. Fix: Google Cloud Console → APIs & Services →\n"
        "    **OAuth consent screen** → scroll to **Test users** → **Add users** → add the exact\n"
        "    Google account you sign in with. Save, wait ~1 min, retry.\n"
        "    (Or sign in with the Google account that **owns** this Cloud project.)\n"
        "  • Half-finished auth: delete calendar_token.json in the repo root and run again.\n"
        "  • Browser cannot complete localhost callback: set GCAL_OAUTH_CONSOLE=1 in .env, run again,\n"
        "    open the printed URL, sign in, then paste the **full redirect URL** from the address bar\n"
        "    (http://localhost:.../?code=...&...) into the terminal.\n"
    )


def _run_manual_oauth_flow(flow: InstalledAppFlow) -> Credentials:
    """No local server: user opens auth URL, then pastes redirect URL or raw code (PKCE-safe if full URL)."""
    port = _oauth_port()
    _print_oauth_troubleshooting(port)
    print("[gcal] Manual OAuth (GCAL_OAUTH_CONSOLE=1): no server on localhost.\n")
    flow.redirect_uri = f"http://localhost:{port}/"
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )
    print(f"Open this URL in your browser:\n{auth_url}\n")
    response = input(
        "After Google signs you in, paste either the **full redirect URL** from the address bar "
        "(best), or only the **code** query value: "
    ).strip()
    if not response:
        raise ValueError("No authorization response pasted.")
    if response.startswith("http"):
        flow.fetch_token(authorization_response=response)
    else:
        flow.fetch_token(code=response)
    creds = flow.credentials
    if not creds:
        raise RuntimeError("OAuth finished but no credentials were produced.")
    return creds


def _run_installed_app_flow(flow: InstalledAppFlow) -> Credentials:
    use_manual = os.getenv("GCAL_OAUTH_CONSOLE", "").strip().lower() in ("1", "true", "yes")
    if use_manual:
        return _run_manual_oauth_flow(flow)

    port = _oauth_port()
    _print_oauth_troubleshooting(port)
    return flow.run_local_server(
        port=port,
        open_browser=True,
        access_type="offline",
        prompt="consent",
    )


def get_calendar_service():
    creds = None

    if TOKEN_FILE.is_file():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDS_FILE.is_file():
                raise FileNotFoundError(
                    f"Missing OAuth client file: {CREDS_FILE} "
                    "(download JSON from Google Cloud Console → APIs & Services → Credentials)"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = _run_installed_app_flow(flow)

        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return build("calendar", "v3", credentials=creds)


def _parse_event_time(start_or_end: dict) -> datetime:
    """Parse Calendar API start/end (dateTime or all-day date)."""
    if "dateTime" in start_or_end:
        s = start_or_end["dateTime"]
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    # All-day: "date": "2026-04-06"; end date is exclusive per API.
    d = start_or_end.get("date") or "1970-01-01"
    return datetime.fromisoformat(d).replace(tzinfo=IST)


def get_todays_events(date: str, *, verbose: bool = True) -> list[dict]:
    service = get_calendar_service()

    start = f"{date}T00:00:00+05:30"
    end = f"{date}T23:59:59+05:30"

    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=start,
            timeMax=end,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    events: list[dict] = []
    for e in result.get("items", []):
        start_dt = _parse_event_time(e.get("start") or {})
        end_dt = _parse_event_time(e.get("end") or {})
        start_ts = start_dt.timestamp()
        end_ts = end_dt.timestamp()

        events.append(
            {
                "id": e["id"],
                "name": e.get("summary", "Unnamed event"),
                "start_ts": start_ts,
                "end_ts": end_ts,
                "start_time": start_dt.astimezone(IST).strftime("%H:%M"),
                "end_time": end_dt.astimezone(IST).strftime("%H:%M"),
                "duration_min": round((end_ts - start_ts) / 60, 1),
            }
        )

    if verbose:
        print(f"Calendar events on {date}:")
        for ev in events:
            print(
                f"  {ev['start_time']} -> {ev['end_time']}  "
                f"{ev['name']} ({ev['duration_min']}m)"
            )

    return events


def _session_bounds_ts(session: dict) -> tuple[float, float]:
    s0 = session["start"]
    s1 = session["end"]
    if isinstance(s0, datetime):
        s_start = s0.timestamp()
    else:
        s_start = datetime.fromisoformat(str(s0).replace("Z", "+00:00")).timestamp()
    if isinstance(s1, datetime):
        s_end = s1.timestamp()
    else:
        s_end = datetime.fromisoformat(str(s1).replace("Z", "+00:00")).timestamp()
    return s_start, s_end


def _session_titles_apps_lower(session: dict) -> tuple[str, list[str]]:
    titles = " ".join(str(t) for t in (session.get("titles") or [])).lower()
    apps = [str(a).lower() for a in (session.get("apps") or [])]
    return titles, apps


def _zoom_present_session(session: dict) -> bool:
    """Zoom app, Zoom URLs, or meeting-like window titles."""
    titles, apps = _session_titles_apps_lower(session)
    urls = " ".join(str(u) for u in (session.get("urls") or [])).lower()
    # IMPORTANT: Only treat as Zoom-present when Zoom is actually detected.
    # Do not use generic "meeting" text — it causes false positives when the calendar has workshop rows.
    return any("zoom" in a for a in apps) or ("zoom.us" in urls) or ("zoom" in titles) or (
        "join from zoom" in titles
    )


def _session_weekday_lower(session: dict) -> str:
    st = session["start"]
    if isinstance(st, datetime):
        dt = st
    else:
        dt = datetime.fromisoformat(str(st).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    else:
        dt = dt.astimezone(IST)
    return dt.strftime("%A").lower()


def _event_is_zoom_extended(event_name: str) -> bool:
    n = event_name.lower()
    return any(kw in n for kw in ZOOM_EXTENDED_EVENTS)


def _match_zoom_extended_events(
    events: list[dict],
    *,
    s_start: float,
    zoom_present: bool,
    workshop_in_title: bool,
    is_workshop_day: bool,
) -> dict | None:
    """Pass 2: workshop / ZOOM_EXTENDED_EVENTS only (after timed overlap on other events)."""
    if not zoom_present:
        return None
    for event in events:
        if not _event_is_zoom_extended(event["name"]):
            continue
        starts_in_slot = event["start_ts"] <= s_start <= event["end_ts"]
        starts_after_slot = event["end_ts"] < s_start <= event["end_ts"] + 7200

        if starts_in_slot and zoom_present:
            return {
                "task_name": event["name"],
                "task_id": event["id"],
                "confidence": 0.95,
                "source": "google_calendar_zoom_extended",
                "note": "Workshop Call — session start in calendar slot with Zoom",
            }

        if starts_after_slot and zoom_present:
            return {
                "task_name": event["name"],
                "task_id": event["id"],
                "confidence": 0.88,
                "source": "zoom_late_start_workshop",
                "note": "Workshop Call — session started within 2h after calendar end with Zoom",
            }

        if workshop_in_title and zoom_present:
            return {
                "task_name": event["name"],
                "task_id": event["id"],
                "confidence": 0.85,
                "source": "zoom_title_workshop_extended",
                "note": "Workshop in window title + Zoom",
            }

        if zoom_present and is_workshop_day:
            return {
                "task_name": event["name"],
                "task_id": event["id"],
                "confidence": 0.80,
                "source": "zoom_workshop_day_heuristic",
                "note": "Workshop day (Mon/Wed/Thu) + Zoom — wide window vs calendar time",
            }

    return None


def match_session_to_calendar(
    session: dict,
    events: list[dict],
    min_overlap_mins: float = 5.0,
) -> dict | None:
    """
    Match a session to a calendar event.

    **Pass 1 — timed overlap (standup, retro, client call, …):** all events that are *not*
    ``ZOOM_EXTENDED_EVENTS``. Requires ≥ ``min_overlap_mins`` overlap and ≥40% of session
    duration overlapping. This runs **before** workshop heuristics so standup wins over
    “Zoom on a workshop day”.

    **Pass 2 — workshop:** ``ZOOM_EXTENDED_EVENTS`` (e.g. Workshop Call): wider rules (slot, late start, title + Zoom, workshop day + Zoom).

    ``CALENDAR_EXACT_EVENTS`` lists typical names for the overlap pass; any non-workshop
    calendar row uses the same overlap logic.
    """
    s_start, s_end = _session_bounds_ts(session)
    titles, _apps = _session_titles_apps_lower(session)
    zoom_present = _zoom_present_session(session)
    weekday = _session_weekday_lower(session)
    workshop_in_title = "workshop" in titles or "workshop call" in titles
    is_workshop_day = weekday in WORKSHOP_CALL_DAYS

    best: dict | None = None
    best_ov = 0.0

    for event in events:
        if _event_is_zoom_extended(event["name"]):
            continue

        overlap_start = max(s_start, event["start_ts"])
        overlap_end = min(s_end, event["end_ts"])
        overlap_mins = (overlap_end - overlap_start) / 60.0
        session_duration_mins = (s_end - s_start) / 60.0
        overlap_pct = overlap_mins / session_duration_mins if session_duration_mins > 0 else 0.0

        if overlap_mins >= min_overlap_mins and overlap_pct >= 0.4:
            if overlap_mins > best_ov:
                best_ov = overlap_mins
                best = {
                    "task_name": event["name"],
                    "task_id": event["id"],
                    "confidence": 0.95,
                    "source": "google_calendar",
                    "overlap_mins": round(overlap_mins, 1),
                }

    if best and best_ov >= min_overlap_mins:
        return best

    return _match_zoom_extended_events(
        events,
        s_start=s_start,
        zoom_present=zoom_present,
        workshop_in_title=workshop_in_title,
        is_workshop_day=is_workshop_day,
    )


def process_sessions_with_calendar(
    sessions: list[dict],
    date: str,
    *,
    verbose_list: bool = False,
    log_matches: bool = True,
) -> tuple[list[dict], list[dict]]:
    """
    If a session overlaps a calendar event by >= 5 min, set zone=meeting and meeting_match
    (Google event id/name; not a ClickUp task id).

    Returns (enriched_sessions, calendar_events) so the caller can use the events
    for further post-processing (e.g. split_sessions_at_meeting_boundaries).
    """
    try:
        events = get_todays_events(date, verbose=verbose_list)
    except Exception as ex:
        print(f"  [calendar] failed to load: {ex}")
        return sessions, []

    if not events:
        print("  [calendar] no events today")
        return sessions, []

    if log_matches:
        print(f"  [calendar] loaded {len(events)} event(s) for {date}")

    enriched: list[dict] = []
    for session in sessions:
        s = dict(session)
        match = match_session_to_calendar(s, events)
        if match:
            s["zone"] = "meeting"
            s["clickup_task_id"] = None
            s["clickup_task_name"] = match["task_name"]
            mm: dict = {
                "matched": True,
                "task_id": match["task_id"],
                "task_name": match["task_name"],
                "confidence": match["confidence"],
                "match_source": match["source"],
            }
            if "overlap_mins" in match:
                mm["overlap_minutes"] = match["overlap_mins"]
            if match.get("note"):
                mm["note"] = match["note"]
            s["meeting_match"] = mm
            if log_matches:
                if "overlap_mins" in match:
                    print(
                        f"  [calendar] matched {match['task_name']} "
                        f"({match['overlap_mins']}m overlap) [{match['source']}]"
                    )
                else:
                    print(
                        f"  [calendar] matched {match['task_name']} "
                        f"[{match['source']}] {match.get('note', '')}"
                    )
        enriched.append(s)

    return enriched, events


if __name__ == "__main__":
    import sys

    d = sys.argv[1] if len(sys.argv) > 1 else datetime.now(IST).strftime("%Y-%m-%d")
    evs = get_todays_events(d, verbose=True)
    print(f"\nTotal: {len(evs)} events")
