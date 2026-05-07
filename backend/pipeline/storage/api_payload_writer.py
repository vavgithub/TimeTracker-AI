"""
Map on-disk pipeline JSON (out/) to Postgres-shaped API payloads.

Source files (writer locations):
  - productivity_{date}.json — pipeline/storage/writer.py::write_daily_summary
  - eod_{date}.json — pipeline/eod/summary_writer.py::generate_eod_summary
  - skill_profile_{week_ending}.json — pipeline/eod/skill_profile.py::write_skill_profile
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from config import OUTPUT_DIR, USER_EMAIL


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _as_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _as_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def productivity_path(out_dir: Path, date: str) -> Path:
    return out_dir / f"productivity_{date}.json"


def eod_path(out_dir: Path, date: str) -> Path:
    return out_dir / f"eod_{date}.json"


def skill_profile_path(out_dir: Path, week_ending: str) -> Path:
    return out_dir / f"skill_profile_{week_ending}.json"


# --- Field mapping docs (Ankit Postgres ↔ pipeline JSON) ---

DAILY_SUMMARY_FIELD_MAP: dict[str, str] = {
    "employee_email": "user (productivity file)",
    "activity_rate": "activity_rate (same name)",
    "deep_work_minutes": "computed.deep_work_minutes (from eod_{date}.json only)",
}

EOD_REPORT_FIELD_MAP: dict[str, str] = {
    "employee_email": "user (eod file; may be null — fallback productivity.user / config)",
    "narrative": "not stored in eod JSON — derived via format_eod_clickup_message(summary)",
    "tasks": "tasks (same name, JSON array)",
    "meetings": "meetings (same name)",
    "performance_signals": "performance_signals (same name)",
    "untracked": "untracked (same name)",
}

SKILL_PROFILE_FIELD_MAP: dict[str, str] = {
    "employee_email": "employee (skill_profile file)",
    "week_ending": "filename date skill_profile_{week_ending}.json (not a field in file)",
    "trend": "no `trend` object in file — built from `consistency` + `period` + `generated_at`",
}


def build_daily_summary_payload(
    date: str,
    *,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Target table DailySummary columns:
      employee_email, date, active_minutes, idle_minutes, activity_rate,
      deep_work_minutes, meeting_minutes, keystrokes, mouse_clicks
    """
    root = out_dir or OUTPUT_DIR
    prod = _read_json(productivity_path(root, date))
    if not isinstance(prod, Mapping):
        prod = {}

    eod = _read_json(eod_path(root, date))
    computed = (eod.get("computed") if isinstance(eod, Mapping) else None) or {}
    if not isinstance(computed, Mapping):
        computed = {}

    deep_work = _as_float(computed.get("deep_work_minutes"), 0.0)

    email = (prod.get("user") or (eod.get("user") if isinstance(eod, Mapping) else None) or USER_EMAIL) or ""
    email = str(email).strip()

    return {
        "employee_email": email,
        "date": str(prod.get("date") or date),
        "active_minutes": _as_float(prod.get("active_minutes"), 0.0),
        "idle_minutes": _as_float(prod.get("idle_minutes"), 0.0),
        "activity_rate": _as_float(prod.get("activity_rate"), 0.0),
        "deep_work_minutes": deep_work,
        "meeting_minutes": _as_float(prod.get("meeting_minutes"), 0.0),
        "keystrokes": _as_int(prod.get("keystrokes"), 0),
        "mouse_clicks": _as_int(prod.get("mouse_clicks"), 0),
    }


def build_eod_report_payload(
    date: str,
    *,
    out_dir: Path | None = None,
    include_narrative: bool = True,
) -> dict[str, Any] | None:
    """
    Target table EodReport columns:
      employee_email, date, narrative, tasks, meetings, performance_signals, untracked
    """
    root = out_dir or OUTPUT_DIR
    eod = _read_json(eod_path(root, date))
    if not isinstance(eod, Mapping):
        return None

    prod = _read_json(productivity_path(root, date))
    if not isinstance(prod, Mapping):
        prod = {}

    email = (eod.get("user") or prod.get("user") or USER_EMAIL) or ""
    email = str(email).strip()

    narrative = ""
    if include_narrative:
        from pipeline.eod.summary_writer import format_eod_clickup_message

        narrative = format_eod_clickup_message(dict(eod))

    return {
        "employee_email": email,
        "date": str(eod.get("date") or date),
        "narrative": narrative,
        "tasks": eod.get("tasks") if eod.get("tasks") is not None else [],
        "meetings": eod.get("meetings") if eod.get("meetings") is not None else [],
        "performance_signals": eod.get("performance_signals")
        if isinstance(eod.get("performance_signals"), (dict, list))
        else {},
        "untracked": eod.get("untracked") if eod.get("untracked") is not None else [],
    }


def build_skill_profile_payload(
    week_ending: str,
    *,
    out_dir: Path | None = None,
) -> dict[str, Any] | None:
    """
    Target table SkillProfile columns:
      employee_email, week_ending, skill_breakdown, top_skill, focus_score, trend
    """
    root = out_dir or OUTPUT_DIR
    raw = _read_json(skill_profile_path(root, week_ending))
    if not isinstance(raw, Mapping):
        return None

    email = str(raw.get("employee") or USER_EMAIL).strip()
    breakdown = raw.get("skill_breakdown")
    if not isinstance(breakdown, dict):
        breakdown = {}

    trend: dict[str, Any] = {
        "consistency": raw.get("consistency"),
        "period": raw.get("period"),
        "generated_at": raw.get("generated_at"),
    }

    return {
        "employee_email": email,
        "week_ending": week_ending,
        "skill_breakdown": breakdown,
        "top_skill": str(raw.get("top_skill") or ""),
        "focus_score": _as_float(raw.get("focus_score"), 0.0),
        "trend": trend,
    }


def build_all_api_payloads(
    date: str,
    week_ending: str | None = None,
    *,
    out_dir: Path | None = None,
) -> dict[str, Any | None]:
    """
    Convenience: daily + eod for `date`, and skill profile for `week_ending`
    (defaults to the same calendar string as `date` when week_ending is omitted).
    """
    root = out_dir or OUTPUT_DIR
    we = week_ending if week_ending is not None else date
    return {
        "daily_summary": build_daily_summary_payload(date, out_dir=root),
        "eod_report": build_eod_report_payload(date, out_dir=root),
        "skill_profile": build_skill_profile_payload(we, out_dir=root),
    }
