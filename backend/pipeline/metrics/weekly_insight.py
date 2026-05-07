from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pipeline.eod.skill_profile import build_skill_profile
from pipeline.metrics.trend_detection import build_performance_trend

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]


IST = timezone(timedelta(hours=5, minutes=30))


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _fmt_hm(mins: float | int) -> str:
    m = int(round(float(mins or 0)))
    if m <= 0:
        return "0m"
    h = m // 60
    r = m % 60
    if h <= 0:
        return f"{m}m"
    if r == 0:
        return f"{h}h"
    return f"{h}h {r}m"


def generate_weekly_insight(
    employee_email: str,
    week_end_date: str,  # YYYY-MM-DD
    out_dir: Path,
) -> dict[str, Any]:
    """
    Reads EOD files for the past 7 days (inclusive ending on week_end_date).
    Reads skill_profile for the week (generates if missing).
    Reads trend report if available (generates if missing).

    Writes: out/weekly_insight_YYYY-MM-DD.json
    """
    out_dir = Path(out_dir)
    end = date.fromisoformat(week_end_date)
    start = end - timedelta(days=6)

    total_active = 0.0
    total_meeting = 0.0
    total_deep_work = 0.0
    total_untracked = 0.0

    tasks_completed = 0
    tasks_within_estimate = 0
    tasks_over_estimate = 0
    avg_conf_sum = 0.0
    avg_conf_days = 0

    tasks_overdue_all: set[str] = set()
    all_tasks: dict[str, float] = defaultdict(float)  # task_name → total minutes

    for i in range(7):
        d = (start + timedelta(days=i)).isoformat()
        eod_path = out_dir / f"eod_{d}.json"
        eod = _read_json(eod_path)
        if not eod:
            continue

        comp = eod.get("computed") if isinstance(eod.get("computed"), dict) else {}
        prod = eod.get("productivity") if isinstance(eod.get("productivity"), dict) else {}

        # Aggregate day totals (prefer computed; fall back to productivity where needed)
        total_active += float(comp.get("tracked_minutes") or prod.get("active_minutes") or 0.0)
        total_deep_work += float(comp.get("deep_work_minutes") or prod.get("task_linked_minutes") or 0.0)

        meeting_from_list = 0.0
        for m in eod.get("meetings") or []:
            if not isinstance(m, dict):
                continue
            meeting_from_list += float(m.get("minutes") or 0.0)
        total_meeting += meeting_from_list or float(prod.get("meeting_minutes") or 0.0)

        total_untracked += float(prod.get("untracked_minutes") or 0.0)

        # Aggregate tasks
        for t in eod.get("tasks") or []:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name") or "").strip()
            mins = float(t.get("today_minutes") or 0.0)
            if name and mins > 0:
                all_tasks[name] += mins

        # Performance signals
        ps = eod.get("performance_signals") if isinstance(eod.get("performance_signals"), dict) else {}
        tasks_completed += int(ps.get("tasks_completed_today") or 0)
        tasks_within_estimate += int(ps.get("tasks_within_estimate") or 0)
        tasks_over_estimate += int(ps.get("tasks_over_estimate") or 0)
        try:
            avg_conf_sum += float(ps.get("avg_map_confidence") or 0.0)
            avg_conf_days += 1
        except Exception:
            pass
        for name in ps.get("overdue_task_names") or []:
            s = str(name).strip()
            if s:
                tasks_overdue_all.add(s)

    # 2) Load or generate skill profile for this week end
    skill_path = out_dir / f"skill_profile_{week_end_date}.json"
    skill_data = _read_json(skill_path)
    if skill_data is None:
        skill_data = build_skill_profile(week_end_date, window="weekly", employee=employee_email)
        skill_path.write_text(json.dumps(skill_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # 3) Load or generate trend
    trend_path = out_dir / f"trend_{week_end_date}.json"
    trend_data = _read_json(trend_path)
    if trend_data is None:
        trend_data = build_performance_trend(employee_email, week_end_date, weeks=4, out_dir=out_dir)

    # 4) Build insight
    top_tasks = sorted(all_tasks.items(), key=lambda x: x[1], reverse=True)[:5]
    avg_conf = round(avg_conf_sum / max(avg_conf_days, 1), 2)

    insight = {
        "week": f"{start.isoformat()} to {end.isoformat()}",
        "employee": employee_email,
        "generated_at": datetime.now(IST).isoformat(),
        "summary": {
            "active_minutes": int(round(total_active)),
            "deep_work_minutes": int(round(total_deep_work)),
            "meeting_minutes": int(round(total_meeting)),
            "untracked_minutes": int(round(total_untracked)),
        },
        "top_tasks": [{"name": name, "minutes": int(round(mins))} for name, mins in top_tasks],
        "skill_breakdown": (skill_data.get("skill_breakdown") if isinstance(skill_data, dict) else {}) or {},
        "top_skill": (skill_data.get("top_skill") if isinstance(skill_data, dict) else "") or "",
        "focus_score": float((skill_data.get("focus_score") if isinstance(skill_data, dict) else 0.0) or 0.0),
        "performance": {
            "tasks_completed": tasks_completed,
            "within_estimate": {"within": tasks_within_estimate, "total_with_estimate": tasks_within_estimate + tasks_over_estimate},
            "tasks_overdue": sorted(tasks_overdue_all),
            "avg_confidence": avg_conf,
            "trend_insight": (trend_data.get("insight") if isinstance(trend_data, dict) else "") or "",
        },
        "trend": trend_data if isinstance(trend_data, dict) else {},
    }

    out_path = out_dir / f"weekly_insight_{week_end_date}.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(insight, indent=2, ensure_ascii=False), encoding="utf-8")
    return insight


def _get_vertex_client():
    project = (os.getenv("GCP_PROJECT_ID") or "").strip()
    region = (os.getenv("GCP_REGION") or "us-central1").strip()
    if genai is None or types is None or not project:
        return None
    try:
        return genai.Client(vertexai=True, project=project, location=region)
    except Exception:
        return None


def format_weekly_clickup_message(insight: dict) -> str:
    """
    Format as ClickUp message. Uses Vertex/Gemini if available; otherwise emits a deterministic message.
    """
    summary = insight.get("summary") if isinstance(insight.get("summary"), dict) else {}
    top_tasks = insight.get("top_tasks") if isinstance(insight.get("top_tasks"), list) else []
    perf = insight.get("performance") if isinstance(insight.get("performance"), dict) else {}

    deterministic = "\n".join(
        [
            f"📊 Week of {insight.get('week')}",
            "",
            f"WORK SUMMARY — active {_fmt_hm(summary.get('active_minutes', 0))} · deep work {_fmt_hm(summary.get('deep_work_minutes', 0))} · meetings {_fmt_hm(summary.get('meeting_minutes', 0))} · untracked {_fmt_hm(summary.get('untracked_minutes', 0))}",
            "",
            "TOP TASKS",
            *[f"  - {t.get('name')} ({_fmt_hm(t.get('minutes', 0))})" for t in top_tasks[:5] if isinstance(t, dict)],
            "",
            f"SKILL FOCUS — {insight.get('top_skill') or '—'} ({float(insight.get('focus_score') or 0):.1f}%)",
            "",
            f"TREND — {perf.get('trend_insight') or 'Not enough data for trend — check back next week'}",
        ]
    ).strip()

    client = _get_vertex_client()
    if client is None:
        return deterministic

    prompt = f"""
Write a concise weekly work insight report for ClickUp.
Data (JSON):
{json.dumps(insight, indent=2)}

Format:
📊 Week of {insight.get('week')}

WORK SUMMARY
  Total active, deep work, meetings, untracked

TOP TASKS
  3-5 tasks with time

SKILL FOCUS
  top skill and percentage

TREND
  one line trend insight

Constraints:
- Under 15 lines
- Professional but conversational
- No task IDs
- Times as \"Xh\", \"Xh Ym\", or \"Xm\"
""".strip()

    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.3),
        )
        text = (resp.text or "").strip()
        return text or deterministic
    except Exception:
        return deterministic

