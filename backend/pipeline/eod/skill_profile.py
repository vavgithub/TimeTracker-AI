"""
Aggregate EOD `skill_category` + meeting minutes across a date window.

Writes ``out/skill_profile_YYYY-MM-DD.json`` where the date is the inclusive end
of the period (last day in the window).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from config import OUTPUT_DIR, USER_EMAIL
from pipeline.eod.summary_writer import categorise_task

WindowKind = Literal["weekly", "monthly"]

_SKILL_ORDER = (
    "development",
    "research",
    "branding",
    "ui_design",
    "motion",
    "client_comms",
    "admin",
    "hiring",
    "general",
    "meeting",
)
_SKILL_RANK = {s: i for i, s in enumerate(_SKILL_ORDER)}


def _out_dir() -> Path:
    return OUTPUT_DIR


def _read_eod(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _daterange_inclusive(start: date, end: date) -> list[date]:
    out: list[date] = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def _window_bounds(end_iso: str, window: WindowKind) -> tuple[date, date, int]:
    end_d = date.fromisoformat(end_iso)
    if window == "monthly":
        n = 30
    else:
        n = 7
    start_d = end_d - timedelta(days=n - 1)
    return start_d, end_d, n


def _normalize_skill(cat: str) -> str:
    c = (cat or "").strip().lower()
    allowed = {
        "branding",
        "ui_design",
        "development",
        "research",
        "motion",
        "client_comms",
        "admin",
        "hiring",
        "general",
        "meeting",
    }
    return c if c in allowed else "general"


def _uniq_preserve(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        s = (n or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _consistency_label(daily_top_skills: list[str], overall_top: str) -> str:
    if len(daily_top_skills) < 2:
        return "low"
    agree = sum(1 for s in daily_top_skills if s == overall_top) / len(daily_top_skills)
    if agree >= 0.55 and len(daily_top_skills) >= 4:
        return "high"
    if agree >= 0.35 or len(daily_top_skills) >= 3:
        return "medium"
    return "low"


def build_skill_profile(
    end_date: str,
    window: WindowKind = "weekly",
    employee: str | None = None,
) -> dict[str, Any]:
    start_d, end_d, _window_days = _window_bounds(end_date, window)
    out_dir = _out_dir()
    period_str = f"{start_d.isoformat()} to {end_d.isoformat()}"

    minutes_by_skill: dict[str, float] = defaultdict(float)
    tasks_by_skill: dict[str, list[str]] = defaultdict(list)
    daily_top_skills: list[str] = []
    employee_resolved = (employee or "").strip()

    for d in _daterange_inclusive(start_d, end_d):
        path = out_dir / f"eod_{d.isoformat()}.json"
        eod = _read_eod(path)
        day_skill_minutes: dict[str, float] = defaultdict(float)

        if eod:
            if not employee_resolved:
                u = str(eod.get("user") or "").strip()
                if u:
                    employee_resolved = u

            for t in eod.get("tasks") or []:
                if not isinstance(t, dict):
                    continue
                fresh_category = categorise_task(
                    str(t.get("name") or ""),
                    str(t.get("parent_name") or ""),
                )
                cat = _normalize_skill(str(fresh_category or "general"))
                mins = float(t.get("today_minutes") or 0.0)
                if mins <= 0:
                    continue
                nm = str(t.get("name") or "").strip()
                minutes_by_skill[cat] += mins
                day_skill_minutes[cat] += mins
                if nm:
                    tasks_by_skill[cat].append(nm)

            for m in eod.get("meetings") or []:
                if not isinstance(m, dict):
                    continue
                mins = float(m.get("minutes") or 0.0)
                if mins <= 0:
                    continue
                nm = str(m.get("name") or "").strip() or "Meeting"
                minutes_by_skill["meeting"] += mins
                day_skill_minutes["meeting"] += mins
                tasks_by_skill["meeting"].append(nm)

        day_total = sum(day_skill_minutes.values())
        if day_total > 0:
            top_day = max(
                day_skill_minutes.keys(),
                key=lambda k: (day_skill_minutes[k], -_SKILL_RANK.get(k, 999)),
            )
            daily_top_skills.append(top_day)

    if not employee_resolved:
        employee_resolved = (USER_EMAIL or "").strip() or "unknown"

    total_minutes = sum(minutes_by_skill.values())
    skill_breakdown: dict[str, Any] = {}

    # Stable ordering: by minutes desc, then skill name
    for skill in sorted(minutes_by_skill.keys(), key=lambda k: (-minutes_by_skill[k], k)):
        m = minutes_by_skill[skill]
        if m <= 0:
            continue
        pct = round(100.0 * m / total_minutes, 1) if total_minutes > 0 else 0.0
        skill_breakdown[skill] = {
            "minutes": int(round(m)),
            "percentage": pct,
            "tasks": _uniq_preserve(tasks_by_skill.get(skill, [])),
        }

    top_skill = (
        max(minutes_by_skill.keys(), key=lambda k: (minutes_by_skill[k], -_SKILL_RANK.get(k, 999)))
        if minutes_by_skill
        else "general"
    )

    focus_score = 0.0
    if total_minutes > 0 and top_skill in minutes_by_skill:
        focus_score = round(100.0 * minutes_by_skill[top_skill] / total_minutes, 1)

    consistency = _consistency_label(daily_top_skills, top_skill)

    return {
        "employee": employee_resolved,
        "period": period_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "skill_breakdown": skill_breakdown,
        "top_skill": top_skill,
        "focus_score": focus_score,
        "consistency": consistency,
    }


def write_skill_profile(
    end_date: str,
    window: WindowKind = "weekly",
    employee: str | None = None,
) -> Path:
    profile = build_skill_profile(end_date, window, employee)
    out_dir = _out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"skill_profile_{end_date}.json"
    out_path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path
