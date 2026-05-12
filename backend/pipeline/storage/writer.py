import json
import hashlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from config import OUTPUT_DIR, USER_EMAIL


def _qtr_hour(hours: float) -> float:
    """Round to nearest 0.25 hour (15 min blocks)."""
    try:
        return round(round(float(hours) / 0.25) * 0.25, 2)
    except Exception:
        return 0.0


def _ensure_out_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_iso(ts) -> str:
    if ts is None:
        return ""
    if isinstance(ts, str):
        return ts
    # datetime
    try:
        return ts.isoformat()
    except Exception:
        return str(ts)


def _stable_session_id(session: dict) -> str:
    key = f"{session.get('start','')}-{session.get('end','')}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _session_ai(s: dict) -> dict:
    ae = s.get("ai_enrichment")
    return ae if isinstance(ae, dict) else {}


def _session_zone(s: dict):
    ai = _session_ai(s)
    z = ai.get("zone")
    if z is None:
        z = s.get("zone")
    return z


def _session_map_method(s: dict):
    ai = _session_ai(s)
    m = ai.get("map_method")
    if m is None:
        m = s.get("map_method")
    return m


def _session_map_confidence(s: dict) -> float:
    ai = _session_ai(s)
    c = ai.get("map_confidence")
    if c is None:
        c = s.get("map_confidence", 0.0)
    try:
        return float(c or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _zone_considered_valid(zone) -> bool:
    if zone is None:
        return False
    z = str(zone).strip().lower()
    return bool(z) and z not in ("unknown", "unclear")


def _existing_session_is_weak(existing: dict) -> bool:
    """True if cached row should lose to a fresh pipeline session."""
    if _session_zone(existing) is None:
        return True
    m = _session_map_method(existing)
    if m is None:
        return True
    if str(m).strip().lower() == "none":
        return True
    return not _zone_considered_valid(_session_zone(existing))


def _pick_merged_session(existing: dict | None, new_s: dict) -> dict:
    if existing is None:
        return new_s
    if _existing_session_is_weak(existing):
        return new_s
    old_conf = _session_map_confidence(existing)
    new_conf = _session_map_confidence(new_s)
    if old_conf >= new_conf:
        return existing
    return new_s


def _write_json_merge(path: Path, new_obj):
    existing = _read_json(path)
    if existing is None:
        path.write_text(json.dumps(new_obj, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    if isinstance(existing, dict) and isinstance(new_obj, dict):
        merged = {**existing, **new_obj}
        path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    # Fallback: if types differ, keep existing and write a sidecar timestamped file.
    sidecar = path.with_suffix(path.suffix + f".{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    sidecar.write_text(json.dumps(new_obj, indent=2, ensure_ascii=False), encoding="utf-8")


def write_sessions(date: str, sessions: list, out_dir: Path | None = None) -> None:
    """
    Writes to out/sessions_YYYY-MM-DD.json.
    Merges with any existing file by session start: fresh pipeline rows win over stale
    unmapped cache unless the cached row has a valid zone and higher map_confidence.
    """
    out_dir = out_dir or _ensure_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"sessions_{date}.json"

    existing = _read_json(out_path)
    existing_items = existing if isinstance(existing, list) else []
    # Deduplicate existing sessions by start timestamp (keep latest occurrence).
    by_start: dict[str, dict] = {}
    for s in existing_items:
        if not isinstance(s, dict):
            continue
        start = str(s.get("start") or "").strip()
        if not start:
            continue
        by_start[start] = s

    normalized = []
    for s in sessions:
        start = _to_iso(s.get("start"))
        end = _to_iso(s.get("end"))

        zone = s.get("zone") or "unclear"
        clickup_task_id = s.get("clickup_task_id", None)
        clickup_task_name = s.get("clickup_task_name", None)
        summary = s.get("summary", "") or ""
        map_confidence = float(s.get("map_confidence", 0.0) or 0.0)
        map_method = s.get("map_method") or "none"
        map_tier = s.get("map_tier")
        map_notes = s.get("map_notes") or ""
        inp = s.get("input") if isinstance(s.get("input"), dict) else {}
        app_bd = s.get("app_breakdown") if isinstance(s.get("app_breakdown"), list) else []

        sid = _stable_session_id({"start": start, "end": end})
        session_obj = {
            "session_id": sid,
            "date": date,
            "user": USER_EMAIL,
            "start": start,
            "end": end,
            # Keep minutes for backward compatibility; hours is the preferred admin view.
            "duration_min": float(s.get("duration_min", 0.0) or 0.0),
            "duration_hours": _qtr_hour(float(s.get("duration_min", 0.0) or 0.0) / 60.0),
            "apps": list(s.get("apps") or []),
            "urls": list(s.get("urls") or []),
            "titles": list(s.get("titles") or []),
            "input": {
                "keystrokes": int(inp.get("keystrokes") or 0),
                "mouse_clicks": int(inp.get("mouse_clicks") or 0),
                "activity_rate": float(inp.get("activity_rate") or 0.0),
                "scroll_units": int(inp.get("scroll_units") or 0),
                "active_minutes": float(inp.get("active_minutes") or 0.0),
                "idle_minutes": float(inp.get("idle_minutes") or 0.0),
            },
            "app_breakdown": list(app_bd),
            "ai_enrichment": {
                "zone": zone,
                "clickup_task_id": clickup_task_id,
                "clickup_task_name": clickup_task_name,
                "map_confidence": map_confidence,
                "map_method": map_method,
                "map_tier": map_tier,
                "map_notes": map_notes,
                "summary": summary,
            },
        }
        normalized.append(session_obj)

    for s in normalized:
        st = str(s.get("start") or "").strip()
        if not st:
            continue
        prev = by_start.get(st)
        by_start[st] = _pick_merged_session(prev, s)

    merged_list = sorted(by_start.values(), key=lambda x: str((x or {}).get("start") or ""))
    out_path.write_text(json.dumps(merged_list, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Written: {out_path} ({len(merged_list)} sessions)")


def write_daily_summary(date: str, breakdown: dict, totals: dict, out_dir: Path | None = None) -> None:
    """
    Writes to out/daily_YYYY-MM-DD.json.
    Never overwrites: merges top-level keys.
    """
    out_dir = out_dir or _ensure_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"daily_{date}.json"

    # Aggregate input totals from sessions file (written just before this call).
    sessions_path = out_dir / f"sessions_{date}.json"
    sessions_obj = _read_json(sessions_path)
    sessions_list = sessions_obj if isinstance(sessions_obj, list) else []

    total_keystrokes = 0
    total_clicks = 0
    total_scroll = 0
    for s in sessions_list:
        if not isinstance(s, dict):
            continue
        ae = s.get("ai_enrichment") if isinstance(s.get("ai_enrichment"), dict) else {}
        inp = ae.get("input") if isinstance(ae.get("input"), dict) else (
            s.get("input") if isinstance(s.get("input"), dict) else {}
        )
        try:
            total_keystrokes += int(inp.get("keystrokes") or 0)
        except Exception:
            pass
        try:
            total_clicks += int(inp.get("mouse_clicks") or 0)
        except Exception:
            pass
        try:
            total_scroll += int(inp.get("scroll_units") or 0)
        except Exception:
            pass

    if isinstance(totals, dict):
        totals["keystrokes"] = int(total_keystrokes)
        totals["mouse_clicks"] = int(total_clicks)
        totals["scroll_units"] = int(total_scroll)

    # Derive optional fields if caller didn't provide them.
    top_urls = totals.get("top_urls", []) if isinstance(totals, dict) else []
    peak_tab_count = totals.get("peak_tab_count", 0) if isinstance(totals, dict) else 0
    active_input = float(totals.get("active_input_minutes", 0.0) or 0.0)
    prod_pct = float(totals.get("productivity_pct", 0.0) or 0.0)
    web_domain_summary = totals.get("web_domain_summary") if isinstance(totals, dict) else None
    if not isinstance(web_domain_summary, dict):
        web_domain_summary = {}

    obj = {
        "date": date,
        "user": USER_EMAIL,
        "totals": {
            "active_minutes": float(totals.get("active_minutes", 0.0) or 0.0),
            "idle_minutes": float(totals.get("idle_minutes", 0.0) or 0.0),
            "active_input_minutes": active_input,
            "productivity_pct": prod_pct,
            "keystrokes": int(totals.get("keystrokes", 0) or 0),
            "mouse_clicks": int(totals.get("mouse_clicks", 0) or 0),
            "scroll_units": int(totals.get("scroll_units", 0) or 0),
            "session_count": int(totals.get("session_count", 0) or 0),
            "task_linked_minutes": float(totals.get("task_linked_minutes", 0.0) or 0.0),
            "meeting_minutes": float(totals.get("meeting_minutes", 0.0) or 0.0),
            "untracked_minutes": float(totals.get("untracked_minutes", 0.0) or 0.0),
            "unclear_minutes": float(totals.get("unclear_minutes", 0.0) or 0.0),
            "unknown_minutes": float(totals.get("unknown_minutes", 0.0) or 0.0),
            # Preferred admin view: rounded hours (0.25h increments).
            "active_hours": _qtr_hour(float(totals.get("active_minutes", 0.0) or 0.0) / 60.0),
            "idle_hours": _qtr_hour(float(totals.get("idle_minutes", 0.0) or 0.0) / 60.0),
            "active_input_hours": _qtr_hour(float(active_input or 0.0) / 60.0),
            "task_linked_hours": _qtr_hour(float(totals.get("task_linked_minutes", 0.0) or 0.0) / 60.0),
            "meeting_hours": _qtr_hour(float(totals.get("meeting_minutes", 0.0) or 0.0) / 60.0),
            "untracked_hours": _qtr_hour(float(totals.get("untracked_minutes", 0.0) or 0.0) / 60.0),
            "unclear_hours": _qtr_hour(float(totals.get("unclear_minutes", 0.0) or 0.0) / 60.0),
            "unknown_hours": _qtr_hour(float(totals.get("unknown_minutes", 0.0) or 0.0) / 60.0),
        },
        "breakdown": {str(k): float(v) for k, v in (breakdown or {}).items()},
        "breakdown_hours": {str(k): _qtr_hour(float(v or 0.0) / 60.0) for k, v in (breakdown or {}).items()},
        "top_urls": list(top_urls),
        "peak_tab_count": int(peak_tab_count or 0),
        "web_domain_summary": web_domain_summary,
        "generated_at": _now_iso(),
    }

    _write_json_merge(out_path, obj)
    print(f"Written: {out_path}")

    # Clean productivity file for admin portal + EOD timesheet.
    productivity_path = out_dir / f"productivity_{date}.json"
    productivity_obj = {
        "date": date,
        "user": USER_EMAIL,
        "active_minutes": float(totals.get("active_minutes", 0.0) or 0.0),
        "idle_minutes": float(totals.get("idle_minutes", 0.0) or 0.0),
        "activity_rate": float(prod_pct or 0.0),
        "keystrokes": int(totals.get("keystrokes", 0) or 0),
        "mouse_clicks": int(totals.get("mouse_clicks", 0) or 0),
        "scroll_units": int(totals.get("scroll_units", 0) or 0),
        "session_count": int(totals.get("session_count", 0) or 0),
        "task_linked_minutes": float(totals.get("task_linked_minutes", 0.0) or 0.0),
        "meeting_minutes": float(totals.get("meeting_minutes", 0.0) or 0.0),
    }
    productivity_path.write_text(
        json.dumps(productivity_obj, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Written: {productivity_path}")


def write_weekly_summary(week_start: str, days: list) -> None:
    """
    Writes to out/weekly_YYYY-Www.json.
    Never overwrites: merges days by date.
    """
    out_dir = _ensure_out_dir()

    # week_label: YYYY-Www (ISO week)
    try:
        dt = datetime.fromisoformat(week_start).date()
        year, week, _ = dt.isocalendar()
        week_label = f"{year}-W{week:02d}"
    except Exception:
        week_label = "unknown-week"

    out_path = out_dir / f"weekly_{week_label}.json"

    existing = _read_json(out_path)
    existing_days = (existing.get("days") if isinstance(existing, dict) else None) or []
    by_date = {
        d.get("date"): d
        for d in existing_days
        if isinstance(d, dict) and d.get("date")
    }
    for d in days or []:
        if isinstance(d, dict) and d.get("date"):
            by_date[d["date"]] = {**by_date.get(d["date"], {}), **d}

    merged_days = [by_date[k] for k in sorted(by_date.keys())]

    active_minutes = sum((d.get("totals", {}) or {}).get("active_minutes", 0.0) for d in merged_days)
    task_linked_minutes = sum((d.get("totals", {}) or {}).get("task_linked_minutes", 0.0) for d in merged_days)
    sessions_completed = sum((d.get("totals", {}) or {}).get("session_count", 0) for d in merged_days)
    avg_daily_active_hours = (active_minutes / 60.0) / len(merged_days) if merged_days else 0.0

    obj = {
        "week_start": week_start,
        "week_label": week_label,
        "user": USER_EMAIL,
        "days": merged_days,
        "totals": {
            "active_hours": float(active_minutes) / 60.0,
            "task_linked_hours": float(task_linked_minutes) / 60.0,
            "sessions_completed": int(sessions_completed),
            "avg_daily_active_hours": float(avg_daily_active_hours),
        },
        "generated_at": _now_iso(),
    }

    _write_json_merge(out_path, obj)
    print(f"Written: {OUTPUT_DIR / f'weekly_{week_label}.json'}")
