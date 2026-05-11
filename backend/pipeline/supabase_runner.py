"""
Runs the full pipeline for a single user using Supabase data.
Replaces the AW localhost reads in main.py with Supabase reads.
Reuses all existing pipeline stages unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import config  # noqa: F401 — loads repo-root .env first
from config import OUTPUT_DIR
from integrations.supabase.adapter import chunks_to_sessions, input_activity_to_daily
from integrations.supabase.client import (
    fetch_chunks_for_user_date,
    fetch_input_activity,
    fetch_task_sessions_for_user,
    fetch_tasks_for_user,
)
from pipeline.eod.skill_profile import write_skill_profile
from pipeline.eod.summary_writer import format_eod_clickup_message, generate_eod_summary
from pipeline.mapping.meeting_mapper import process_meeting_sessions
from pipeline.mapping.task_mapper import map_sessions_to_tasks
from pipeline.storage.push_to_server import push_daily_summary, push_eod_report, push_skill_profile
from pipeline.storage.writer import write_daily_summary, write_sessions


def run_for_user(
    user_id: str,
    user_email: str,
    date_str: str,
    out_dir: Path | None = None,
    post_eod: bool = False,
    push: bool = True,
) -> dict[str, Any]:
    """
    Full pipeline run for one user for one date.
    Returns summary dict with status and key metrics.
    """
    if out_dir is None:
        out_dir = OUTPUT_DIR / user_id
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[supabase_runner] processing user={user_email} date={date_str}")

    # 1. Fetch chunks from Supabase
    chunks = fetch_chunks_for_user_date(user_id, date_str)
    print(f"[supabase_runner] fetched {len(chunks)} chunks")

    if not chunks:
        print(f"[supabase_runner] no chunks found for {user_email} on {date_str} — skipping")
        return {"status": "no_data", "user": user_email, "date": date_str}

    # 2. Convert chunks to sessions
    sessions = chunks_to_sessions(chunks)
    print(f"[supabase_runner] built {len(sessions)} sessions from chunks")

    # 3. Fetch input activity
    input_day = fetch_input_activity(user_id, date_str)
    input_metrics = input_activity_to_daily(input_day)
    print(f"[supabase_runner] input: {input_metrics}")

    # 4. Tasks + task sessions from Supabase (Task / TaskSession tables)
    tasks = fetch_tasks_for_user(user_id)
    time_entries = fetch_task_sessions_for_user(user_id, date_str)
    print(f"[supabase_runner] Task/TaskSession: {len(tasks)} tasks, {len(time_entries)} time entries")

    # 5. Map sessions to tasks
    daily_context = {
        "employee_name": user_email.split("@")[0],
        "meetings_today": [],
    }
    results = map_sessions_to_tasks(
        sessions,
        tasks,
        time_entries,
        daily_context=daily_context,
    )

    # 6. Refine meetings
    results = process_meeting_sessions(results, None, debug=False)

    # 7. Compute totals
    from collections import defaultdict

    zone_minutes: dict[str, float] = defaultdict(float)
    for r in results:
        z = r.get("zone") or "unclear"
        zone_minutes[z] += float(r.get("duration_min", 0.0) or 0.0)

    active_minutes = sum(r.get("duration_min", 0.0) or 0.0 for r in results)
    meeting_minutes = float(zone_minutes.get("meeting", 0.0))
    task_linked_minutes = float(zone_minutes.get("task_linked", 0.0))

    totals = {
        "active_minutes": active_minutes,
        "idle_minutes": sum(c.get("afkMs", 0) or 0 for c in chunks) / 60000,
        "active_input_minutes": active_minutes,
        "productivity_pct": round(
            sum(s.get("activity_rate", 0) for s in sessions) / max(len(sessions), 1), 1
        ),
        "session_count": len(results),
        "task_linked_minutes": task_linked_minutes,
        "meeting_minutes": meeting_minutes,
        "untracked_minutes": float(zone_minutes.get("untracked_work", 0.0)),
        "unclear_minutes": float(zone_minutes.get("unclear", 0.0)),
        "top_urls": [],
        "peak_tab_count": 0,
        "web_domain_summary": {"domains_top": []},
        **input_metrics,
    }

    # 8. Write output files
    write_sessions(date_str, results, out_dir=out_dir)
    write_daily_summary(date_str, dict(zone_minutes), totals, out_dir=out_dir)

    # 9. Generate EOD
    eod = generate_eod_summary(date_str, user_email, out_dir=out_dir)

    # Push daily-summary + skill profile (daily window; week_ending in API = date_str)
    if push:
        push_daily_summary(date_str, out_dir)
        day_key = (date_str or "").strip()[:10]
        write_skill_profile(day_key, "daily", user_email, out_dir=out_dir)
        push_skill_profile(day_key, out_dir)

    if post_eod:
        _ = format_eod_clickup_message(eod)

        if push:
            push_eod_report(date_str, out_dir)
            print(f"[supabase_runner] pushed EOD for {user_email}")

    return {
        "status": "ok",
        "user": user_email,
        "date": date_str,
        "sessions": len(results),
        "active_minutes": round(active_minutes, 1),
        "tasks_mapped": sum(1 for r in results if r.get("zone") == "task_linked"),
    }

