import math
from datetime import datetime

import requests

from config import AFK_BUCKET, BASE_URL, INPUT_BUCKET


def _events_or_empty(raw) -> list:
    """ActivityWatch returns a JSON list of events; errors/objects become []."""
    return raw if isinstance(raw, list) else []


def fetch_input_and_afk_events(date: str) -> tuple[list, list]:
    """Raw ActivityWatch bucket events for input + AFK (same window as get_daily_input_summary)."""
    try:
        input_raw = requests.get(
            f"{BASE_URL}/buckets/{INPUT_BUCKET}/events",
            params={
                "start": f"{date}T00:00:00+05:30",
                "end": f"{date}T23:59:59+05:30",
                "limit": 2000,
            },
            timeout=30,
        ).json()

        afk_raw = requests.get(
            f"{BASE_URL}/buckets/{AFK_BUCKET}/events",
            params={
                "start": f"{date}T00:00:00+05:30",
                "end": f"{date}T23:59:59+05:30",
                "limit": 2000,
            },
            timeout=30,
        ).json()
    except requests.RequestException:
        return [], []
    except Exception:
        return [], []

    return _events_or_empty(input_raw), _events_or_empty(afk_raw)


def aggregate_daily_input_from_events(input_events: list, afk_events: list) -> dict:
    """Same numbers as get_daily_input_summary but without fetching buckets."""
    keystrokes = sum(e["data"].get("presses", 0) for e in input_events)
    mouse_clicks = sum(e["data"].get("clicks", 0) for e in input_events)
    scroll_y = sum(e["data"].get("scrollY", 0) for e in input_events)
    delta_x = sum(e["data"].get("deltaX", 0) for e in input_events)
    delta_y = sum(e["data"].get("deltaY", 0) for e in input_events)
    mouse_dist = round(math.sqrt(delta_x**2 + delta_y**2))

    active_secs = sum(e["duration"] for e in afk_events if e["data"].get("status") == "not-afk")
    idle_secs = sum(e["duration"] for e in afk_events if e["data"].get("status") == "afk")
    total_secs = active_secs + idle_secs
    activity_rate = round(active_secs / total_secs * 100, 1) if total_secs > 0 else 0.0

    return {
        "active_minutes": round(active_secs / 60, 1),
        "idle_minutes": round(idle_secs / 60, 1),
        "activity_rate": activity_rate,
        "keystrokes": keystrokes,
        "mouse_clicks": mouse_clicks,
        "scroll_units": scroll_y,
        "mouse_distance": mouse_dist,
    }


def get_daily_input_summary(date: str) -> dict:
    input_events, afk_events = fetch_input_and_afk_events(date)
    out = aggregate_daily_input_from_events(input_events, afk_events)
    out["date"] = date
    out["input_source"] = "input_watcher + afk_watcher"
    return out


def _ts_to_unix(iso_or_dt) -> float:
    if isinstance(iso_or_dt, datetime):
        return iso_or_dt.timestamp()
    s = str(iso_or_dt).replace("Z", "+00:00")
    return datetime.fromisoformat(s).timestamp()


def get_session_input_summary(session: dict, input_events: list, afk_events: list) -> dict:
    """Raw input counts for a specific session window."""
    s_start = _ts_to_unix(session["start"])
    s_end = _ts_to_unix(session["end"])

    keystrokes = clicks = scroll = 0.0
    active = idle = 0.0

    for e in input_events:
        e_start = _ts_to_unix(e["timestamp"])
        e_end = e_start + e["duration"]
        overlap = max(0.0, min(s_end, e_end) - max(s_start, e_start))
        if overlap > 0:
            ratio = overlap / e["duration"]
            keystrokes += e["data"].get("presses", 0) * ratio
            clicks += e["data"].get("clicks", 0) * ratio
            scroll += e["data"].get("scrollY", 0) * ratio

    for e in afk_events:
        e_start = _ts_to_unix(e["timestamp"])
        e_end = e_start + e["duration"]
        overlap = max(0.0, min(s_end, e_end) - max(s_start, e_start))
        if overlap > 0:
            if e["data"].get("status") == "not-afk":
                active += overlap
            else:
                idle += overlap

    total = active + idle
    return {
        "active_minutes": round(active / 60, 1),
        "idle_minutes": round(idle / 60, 1),
        "activity_rate": round(active / total * 100, 1) if total > 0 else 0.0,
        "keystrokes": round(keystrokes),
        "mouse_clicks": round(clicks),
        "scroll_units": round(scroll),
    }


if __name__ == "__main__":
    result = get_daily_input_summary("2026-04-02")

    print("\n" + "=" * 48)
    print(f"INPUT SUMMARY — {result['date']}")
    print("=" * 48)
    print(f"Active:        {result['active_minutes']} min")
    print(f"Idle:          {result['idle_minutes']} min")
    print(f"Activity rate: {result['activity_rate']}%")
    print()
    print(f"Keystrokes:    {result['keystrokes']}")
    print(f"Mouse clicks:  {result['mouse_clicks']}")
    print(f"Scroll:        {result['scroll_units']} units")
    print("=" * 48)

    print("\nSan sees:")
    print(
        f"  Khyathi · {result['date']} · "
        f"{result['active_minutes']}m active · "
        f"{result['keystrokes']} keystrokes · "
        f"{result['mouse_clicks']} clicks"
    )
