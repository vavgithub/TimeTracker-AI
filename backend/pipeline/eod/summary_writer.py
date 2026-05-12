import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

from config import OUTPUT_DIR
from integrations.clickup.client import ClickUpClient
from utils.helpers import get_domain

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]


IST = timezone(timedelta(hours=5, minutes=30))

_CATEGORY_CACHE: dict[tuple[str, str, str], str] = {}
_CATEGORY_CACHE_CLEARED_ONCE = False


def clear_category_cache() -> None:
    """
    Clear category cache.

    Note: this repo currently only caches in-memory per process run (no on-disk cache file).
    """
    global _CATEGORY_CACHE
    _CATEGORY_CACHE.clear()


def _ensure_out_dir() -> Path:
    out_dir = OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _parse_iso_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _to_ist_hm(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    try:
        return dt.astimezone(IST).strftime("%H:%M")
    except Exception:
        return None


def _to_date_ist(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    try:
        return dt.astimezone(IST).strftime("%Y-%m-%d")
    except Exception:
        return None


def _safe_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _get_vertex_client():
    project = os.getenv("GCP_PROJECT_ID", "").strip()
    region = os.getenv("GCP_REGION", "us-central1").strip()
    if genai is None or types is None or not project:
        return None
    try:
        return genai.Client(vertexai=True, project=project, location=region)
    except Exception:
        return None


def categorise_task(task_name: str, parent_name: str | None) -> str:
    """
    Categorise ClickUp tasks using AI.
    Caches results per pipeline run to avoid repeat calls.
    """
    global _CATEGORY_CACHE_CLEARED_ONCE
    if not _CATEGORY_CACHE_CLEARED_ONCE:
        clear_category_cache()
        _CATEGORY_CACHE_CLEARED_ONCE = True

    tn = (task_name or "").strip()
    pn = (parent_name or "").strip()
    employee_role = (os.getenv("EMPLOYEE_ROLE", "general") or "general").strip() or "general"

    key = (tn.lower(), pn.lower(), employee_role.lower())
    if key in _CATEGORY_CACHE:
        return _CATEGORY_CACHE[key]

    client = _get_vertex_client()
    if client is None:
        cat = "general"
        _CATEGORY_CACHE[key] = cat
        return cat

    prompt = f"""
You are categorising a work task for an employee
at Value at Void, a design and technology agency.

Employee role: {employee_role}
Task name: {tn}
Parent task: {pn or 'none'}

Based on the employee's role, determine what category
of work this task represents.

For example:
- An \"AI Developer\" working on any software, system,
  pipeline, tracker, dashboard, or automation tool
  → that is \"development\" work for them
- A \"Designer\" working on any visual, UI, or brand task
  → that is their primary skill category
- An \"HR\" person doing hiring or people management
  → that is their primary skill category

Categories:
  development  - software, AI, code, systems, automation
  ui_design    - UI/UX, design, Figma, visual components
  branding     - brand identity, logos, guidelines
  research     - analysis, research, audits
  motion       - animation, video, motion
  client_comms - client calls, presentations
  hiring       - interviews, screening, recruitment
  admin        - purely administrative (leaves, HR forms)
  general      - unclear

If the parent task is a software/development project
and this subtask is called 'Research', classify as
development — it's research within a dev project,
not standalone academic research.

Consider: would an {employee_role} working on
\"{tn}\" be doing their core role work?
If yes → use their primary skill category.
If it's administrative overhead → use admin.
If unclear → use general.

Reply with ONLY the category word.
""".strip()

    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0),
        )
        text = (resp.text or "").strip().split()
        cat = (text[0] if text else "general").strip().lower()
    except Exception:
        cat = "general"

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
    }
    if cat not in allowed:
        cat = "general"

    _CATEGORY_CACHE[key] = cat
    return cat


@dataclass(frozen=True)
class _TaskKey:
    task_id: str


def _extract_task_id(session_obj: dict) -> str | None:
    if not isinstance(session_obj, dict):
        return None
    ae = session_obj.get("ai_enrichment") if isinstance(session_obj.get("ai_enrichment"), dict) else {}
    tid = ae.get("clickup_task_id")
    if tid is None:
        tid = session_obj.get("clickup_task_id")
    if tid is None:
        return None
    tid_s = str(tid).strip()
    return tid_s or None


def _extract_task_name(s: dict) -> str | None:
    ae = s.get("ai_enrichment") if isinstance(s.get("ai_enrichment"), dict) else {}
    return ae.get("clickup_task_name") or s.get("clickup_task_name")


def _extract_session_confidence(session_obj: dict) -> float | None:
    if not isinstance(session_obj, dict):
        return None
    ae = session_obj.get("ai_enrichment") if isinstance(session_obj.get("ai_enrichment"), dict) else {}
    if "map_confidence" in ae:
        return _safe_float(ae.get("map_confidence"), default=0.0)
    if "map_confidence" in session_obj:
        return _safe_float(session_obj.get("map_confidence"), default=0.0)
    return None


def _session_zone(s: dict) -> str:
    if not isinstance(s, dict):
        return "unknown"
    ae = s.get("ai_enrichment") if isinstance(s.get("ai_enrichment"), dict) else {}
    z = s.get("zone") or ae.get("zone") or "unknown"
    return str(z)


def _extract_zone(session_obj: dict) -> str:
    return _session_zone(session_obj)


_TITLE_HINT_MAX = 30
# ClickUp / session titles often use "Parent — Subtask" (em dash, spaces).
_EM_DASH_SEP = f" {chr(0x2014)} "


def _dedupe_segments_by_id(segment_rows: list[dict]) -> list[dict]:
    """Keep first occurrence of each segment id so duration is not double-counted."""
    seen: set[str] = set()
    out: list[dict] = []
    for seg in segment_rows:
        if not isinstance(seg, dict):
            continue
        sid = seg.get("id")
        if sid is not None and str(sid) != "":
            k = str(sid)
            if k in seen:
                continue
            seen.add(k)
        out.append(seg)
    return out


def _split_em_dash_display(full: str) -> tuple[str, str | None]:
    """If name looks like 'Parent — Subtask', return (subtask, parent); else (full, None)."""
    s = (full or "").strip()
    if not s:
        return "", None
    if _EM_DASH_SEP in s:
        parts = s.split(_EM_DASH_SEP, 1)
        if len(parts) == 2:
            parent, sub = parts[0].strip(), parts[1].strip()
            if parent and sub:
                return sub, parent
    return s, None


def _segment_match_aliases(raw_title: str, display_name: str, em_parent: str | None) -> list[str]:
    """Names that may appear on segments.task_name for this ClickUp task."""
    raw = (raw_title or "").strip()
    disp = (display_name or "").strip()
    out: list[str] = []
    for x in (raw, disp, f"{em_parent}{_EM_DASH_SEP}{disp}" if em_parent else ""):
        if x and x not in out:
            out.append(x)
    return out


def _strip_title_suffixes(title: str) -> str:
    """Remove common window-title suffixes; result is the display hint base."""
    s = (title or "").strip()
    if not s:
        return ""
    changed = True
    while changed:
        changed = False
        for suf in (
            " - Google Chrome",
            " - Brave",
            "| Value at Void ™",
            "| Value at Void™",
        ):
            if s.endswith(suf):
                s = s[: -len(suf)].rstrip()
                changed = True
        if re.search(r" - .+ - Cursor$", s):
            s = re.sub(r" - .+ - Cursor$", "", s).rstrip()
            changed = True
    return s.strip()


def _title_hint_from_raw(raw_title: str) -> str:
    base = _strip_title_suffixes(raw_title)
    if not base:
        return ""
    if len(base) > _TITLE_HINT_MAX:
        return base[:_TITLE_HINT_MAX]
    return base


def _load_segment_rows_for_date(summary_date: str, out_dir: Path | None = None) -> list[dict]:
    out_dir = out_dir or _ensure_out_dir()
    path = out_dir / f"segments_{summary_date}.json"
    obj = _read_json(path)
    if not isinstance(obj, dict):
        return []
    segs = obj.get("segments")
    if not isinstance(segs, list):
        return []
    rows = [s for s in segs if isinstance(s, dict)]
    return _dedupe_segments_by_id(rows)


def _segment_matches_task_names(seg: dict, names_lower: set[str]) -> bool:
    sn = (seg.get("task_name") or "").strip().lower()
    return bool(sn) and sn in names_lower


def _build_tools_for_task_name(
    segment_rows: list[dict],
    match_names: list[str],
    cap_total_minutes: float | None = None,
) -> list[dict]:
    """Group segments whose task_name matches any alias: by app, sum minutes, title_hint; top 3; cap totals."""
    names_lower = {(n or "").strip().lower() for n in match_names if (n or "").strip()}
    if not names_lower:
        return []

    matched: list[dict] = []
    for seg in segment_rows:
        if _segment_matches_task_names(seg, names_lower):
            matched.append(seg)

    minutes_by_app: dict[str, float] = defaultdict(float)
    titles_by_app: dict[str, list[str]] = defaultdict(list)
    for seg in matched:
        app = str(seg.get("app") or "").strip() or "unknown"
        dur = _safe_float(seg.get("duration_minutes"), 0.0)
        if dur <= 0:
            continue
        title = str(seg.get("title") or "").strip()
        minutes_by_app[app] += dur
        titles_by_app[app].append(title)

    rows: list[dict] = []
    for app, minutes_f in minutes_by_app.items():
        titles = titles_by_app.get(app) or []
        non_empty = [x for x in titles if str(x).strip()]
        if non_empty:
            raw_mode = Counter(non_empty).most_common(1)[0][0]
            hint = _title_hint_from_raw(str(raw_mode))
        else:
            hint = ""
        if not (hint or "").strip():
            hint = app if app != "unknown" else ""
        else:
            hint = hint.strip()
        rows.append(
            {
                "app": app,
                "title_hint": hint[:_TITLE_HINT_MAX] if len(hint) > _TITLE_HINT_MAX else hint,
                "minutes": int(round(minutes_f)),
            }
        )

    rows.sort(key=lambda r: (-_safe_int(r.get("minutes"), 0), str(r.get("app") or "")))
    rows = rows[:3]

    if cap_total_minutes is not None and cap_total_minutes > 0 and rows:
        total_tools = float(sum(_safe_int(r.get("minutes"), 0) for r in rows))
        if total_tools > cap_total_minutes and total_tools > 0:
            scale = float(cap_total_minutes) / total_tools
            for r in rows:
                r["minutes"] = max(0, int(round(_safe_int(r.get("minutes"), 0) * scale)))

    return rows


_NOISE_TITLES_FOR_NARRATIVE = frozenset({"loading", "unknown", "", "new tab", "untitled"})


def _session_app_minutes_from_breakdown(session: dict) -> dict[str, float]:
    """Per-app minutes from one session's app_breakdown (AW + Supabase shapes)."""
    per_app: dict[str, float] = defaultdict(float)
    bd = session.get("app_breakdown")
    if not isinstance(bd, list):
        return dict(per_app)
    for entry in bd:
        if not isinstance(entry, dict):
            continue
        app = str(entry.get("app") or "unknown").strip() or "unknown"
        if entry.get("durationMs") is not None:
            per_app[app] += float(entry.get("durationMs") or 0) / 60000.0
        elif entry.get("total_minutes") is not None:
            per_app[app] += float(entry.get("total_minutes") or 0)
        elif entry.get("minutes") is not None:
            per_app[app] += float(entry.get("minutes") or 0)
        elif isinstance(entry.get("tabs"), list):
            tab_sum = 0.0
            for tab in entry["tabs"]:
                if isinstance(tab, dict):
                    tab_sum += float(tab.get("minutes") or tab.get("duration_minutes") or 0)
            if tab_sum > 0:
                per_app[app] += tab_sum
    return dict(per_app)


def _narrative_metrics_from_sessions(sessions_list: list) -> tuple[str, str, float, int]:
    """Top apps label, window titles label, mean activity %, task-linked session count."""
    noise = _NOISE_TITLES_FOR_NARRATIVE
    app_totals: dict[str, float] = defaultdict(float)
    seen_titles: set[str] = set()
    ordered_titles: list[str] = []
    rates: list[float] = []
    task_linked = 0

    for s in sessions_list:
        if not isinstance(s, dict):
            continue
        if str(_extract_zone(s) or "").lower().strip() == "task_linked":
            task_linked += 1
        for app, mins in _session_app_minutes_from_breakdown(s).items():
            if app.lower() in ("", "unknown"):
                continue
            app_totals[app] += mins
        for t in s.get("titles") or []:
            if len(ordered_titles) >= 10:
                break
            tt = str(t).strip()
            if len(tt) < 3 or tt.lower() in noise:
                continue
            if tt not in seen_titles:
                seen_titles.add(tt)
                ordered_titles.append(tt)
        if len(ordered_titles) < 10:
            bd = s.get("app_breakdown")
            if isinstance(bd, list):
                for entry in bd:
                    if len(ordered_titles) >= 10:
                        break
                    if not isinstance(entry, dict):
                        continue
                    for tit in entry.get("titles") or []:
                        if len(ordered_titles) >= 10:
                            break
                        tt = str(tit).strip()
                        if len(tt) < 3 or tt.lower() in noise:
                            continue
                        if tt not in seen_titles:
                            seen_titles.add(tt)
                            ordered_titles.append(tt)
                    tabs = entry.get("tabs")
                    if isinstance(tabs, list):
                        for tab in tabs:
                            if len(ordered_titles) >= 10:
                                break
                            if not isinstance(tab, dict):
                                continue
                            tit = tab.get("title")
                            if not tit:
                                continue
                            tt = str(tit).strip()
                            if len(tt) < 3 or tt.lower() in noise:
                                continue
                            if tt not in seen_titles:
                                seen_titles.add(tt)
                                ordered_titles.append(tt)
        inp = s.get("input")
        r = None
        if isinstance(inp, dict) and inp.get("activity_rate") is not None:
            r = inp.get("activity_rate")
        if r is None and s.get("activity_rate") is not None:
            r = s.get("activity_rate")
        if r is not None:
            try:
                rates.append(float(r))
            except (TypeError, ValueError):
                pass

    if app_totals:
        top_apps = ", ".join(
            f"{a} (~{round(m)} min)"
            for a, m in sorted(app_totals.items(), key=lambda x: x[1], reverse=True)[:8]
        )
    else:
        top_apps = "None"
    top_titles = "; ".join(ordered_titles[:10]) if ordered_titles else "None"
    avg_activity = round(sum(rates) / len(rates), 1) if rates else 0.0
    return top_apps, top_titles, avg_activity, task_linked


def _generate_narrative_from_sessions(
    employee: str,
    summary_date: str,
    tasks: list[dict],
    meetings: list[dict],
    tracked_minutes: float,
    session_count: int,
    sessions_list: list,
) -> str:
    try:
        from pipeline.mapping.task_mapper import _get_vertex_client

        client = _get_vertex_client()
        if not client:
            return ""

        top_apps, top_titles, avg_activity, task_linked_sessions = _narrative_metrics_from_sessions(
            sessions_list
        )

        task_lines = "\n".join(
            f"- {t.get('name', '')} ({round(float(t.get('today_minutes') or 0))} min)"
            for t in tasks
            if isinstance(t, dict)
        )
        meeting_lines = (
            "\n".join(
                f"- {m.get('name', 'Meeting')} ({int(m.get('minutes') or 0)} min)"
                for m in meetings
                if isinstance(m, dict)
            )
            if meetings
            else "None"
        )

        prompt = f"""Write a 2-3 sentence professional EOD summary for {employee}.
Date: {summary_date}
Total tracked: {round(tracked_minutes)} minutes across {session_count} sessions
Task-linked sessions: {task_linked_sessions}

Tasks worked on:
{task_lines if task_lines else "No tasks mapped"}

Meetings:
{meeting_lines}

Top apps: {top_apps}
Key activities (window titles): {top_titles}
Average activity rate: {avg_activity}%

Write in third person. Be specific about what was worked on.
Keep it concise and professional. No bullet points."""

        from google.genai import types as genai_types

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                max_output_tokens=1000,
                temperature=0.3,
            ),
        )
        return response.text.strip() if response.text else ""
    except Exception as e:
        print(f"[eod] narrative generation failed: {e}")
        return ""


def generate_eod_summary(
    summary_date: str,
    user_email: str,
    *,
    skip_ai: bool = False,
    out_dir: Path | None = None,
) -> dict:
    out_dir = out_dir or _ensure_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    sessions_path = out_dir / f"sessions_{summary_date}.json"
    productivity_path = out_dir / f"productivity_{summary_date}.json"
    segment_rows = _load_segment_rows_for_date(summary_date, out_dir=out_dir)
    existing_eod_path = out_dir / f"eod_{summary_date}.json"
    existing_eod = _read_json(existing_eod_path) if skip_ai else None
    existing_skill_by_id: dict[str, str] = {}
    existing_skill_by_name: dict[str, str] = {}
    if skip_ai and isinstance(existing_eod, dict):
        for t in existing_eod.get("tasks") or []:
            if not isinstance(t, dict):
                continue
            cat = str(t.get("skill_category") or "").strip().lower()
            if not cat:
                continue
            tid = t.get("task_id") or t.get("id")
            if tid is not None and str(tid).strip():
                existing_skill_by_id[str(tid).strip()] = cat
            nm = str(t.get("name") or "").strip().lower()
            if nm:
                existing_skill_by_name[nm] = cat

    sessions_obj = _read_json(sessions_path)
    sessions = sessions_obj if isinstance(sessions_obj, list) else []
    prod = _read_json(productivity_path) if productivity_path.exists() else {}
    if not isinstance(prod, dict):
        prod = {}

    # Segment-based totals (work-hours filtered to avoid overnight bleed).
    def is_work_segment(seg: dict, d: str) -> bool:
        start = seg.get("start", "")
        if not start:
            return False
        try:
            dt = datetime.fromisoformat(str(start)).astimezone(IST)
            if dt.date().isoformat() != d:
                return False
            if dt.hour < 8 or dt.hour >= 21:
                return False
            return True
        except Exception:
            return False

    work_segments = [s for s in segment_rows if isinstance(s, dict) and is_work_segment(s, summary_date)]
    assert len(work_segments) <= len(segment_rows)
    print(f"All segments: {len(segment_rows)} Work segments: {len(work_segments)}")

    if len(work_segments) == 0:
        # Supabase (and other) pipelines often have no segment file — derive EOD from sessions JSON.
        sessions_list = sessions
        task_totals: dict[str, dict] = {}
        for s in sessions_list:
            if not isinstance(s, dict):
                continue
            zone = str(_extract_zone(s) or "").lower().strip()
            if zone != "task_linked":
                continue
            task_id = _extract_task_id(s)
            task_name = _extract_task_name(s)
            if not task_id or not task_name:
                continue
            dur = float(s.get("duration_min") or 0.0)
            if task_id not in task_totals:
                task_totals[task_id] = {
                    "task_id": task_id,
                    "name": task_name,
                    "today_minutes": 0.0,
                    "parent_name": None,
                    "task_em_dash_parent": None,
                    "segment_match_names": [],
                    "status": None,
                    "time_estimate_minutes": None,
                    "due_date": None,
                    "is_overdue": False,
                    "days_overdue": 0,
                    "percent_of_estimate": None,
                    "avg_confidence": None,
                    "skill_category": "general",
                    "tools": [],
                }
            task_totals[task_id]["today_minutes"] += dur

        tasks_out_fb: list[dict] = []
        for row in task_totals.values():
            row["today_minutes"] = round(float(row["today_minutes"]), 1)
            row["skill_category"] = categorise_task(str(row.get("name") or ""), "")
            tasks_out_fb.append(row)

        meetings_out_fb: list[dict] = []
        for s in sessions_list:
            if not isinstance(s, dict):
                continue
            if str(_extract_zone(s) or "").lower().strip() != "meeting":
                continue
            name = _extract_task_name(s) or "Meeting"
            dur = float(s.get("duration_min") or 0.0)
            if dur <= 0:
                continue
            meetings_out_fb.append({"name": name, "minutes": int(round(dur))})

        tracked_minutes_fb = sum(
            float(s.get("duration_min") or 0.0)
            for s in sessions_list
            if isinstance(s, dict)
            and str(_extract_zone(s) or "").lower().strip() in ("task_linked", "meeting")
        )
        deep_work_fb = sum(float(t.get("today_minutes") or 0.0) for t in tasks_out_fb)

        performance_signals_fb = {
            "tasks_completed_today": [],
            "tasks_overdue": [],
            "tasks_over_estimate": [],
            "tasks_within_estimate": [],
        }
        session_count_fb = sum(1 for s in sessions_list if isinstance(s, dict))

        narrative = _generate_narrative_from_sessions(
            user_email,
            summary_date,
            tasks_out_fb,
            meetings_out_fb,
            tracked_minutes_fb,
            len(sessions_list),
            sessions_list,
        )

        summary_fb = {
            "date": summary_date,
            "user": user_email,
            "narrative": narrative
            or (
                f"EOD — {summary_date} (IST)\n"
                f"Tracked: {int(round(tracked_minutes_fb))}m | Sessions: {session_count_fb}"
            ),
            "productivity": prod,
            "computed": {
                "tracked_minutes": round(tracked_minutes_fb, 1),
                "deep_work_minutes": round(deep_work_fb, 1),
            },
            "session_count": session_count_fb,
            "tasks": tasks_out_fb,
            "performance_signals": performance_signals_fb,
            "meetings": meetings_out_fb,
            "untracked": [],
            "low_confidence_sessions": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        out_path_fb = out_dir / f"eod_{summary_date}.json"
        out_path_fb.write_text(json.dumps(summary_fb, indent=2, ensure_ascii=False), encoding="utf-8")
        return summary_fb

    task_minutes = sum(
        _safe_float(s.get("duration_minutes"), 0.0)
        for s in work_segments
        if str(_session_zone(s) or "").lower() == "task_linked"
    )
    meeting_minutes = sum(
        _safe_float(s.get("duration_minutes"), 0.0)
        for s in work_segments
        if str(_session_zone(s) or "").lower() == "meeting"
    )
    untracked_minutes = sum(
        _safe_float(s.get("duration_minutes"), 0.0)
        for s in work_segments
        if str(_session_zone(s) or "").lower() == "untracked_work"
    )

    active_minutes = task_minutes + meeting_minutes + untracked_minutes
    tracked_minutes = sum(_safe_float(s.get("duration_minutes"), 0.0) for s in work_segments)
    deep_work_minutes = task_minutes

    # Aggregate sessions by task_id
    confs_by_task: dict[str, list[float]] = defaultdict(list)
    low_conf_sessions: list[dict] = []
    untracked: list[dict] = []

    # Task totals from segments (work-hours only)
    minutes_by_task: dict[str, float] = defaultdict(float)
    segment_task_name_by_id: dict[str, str] = {}
    for seg in work_segments:
        if str(_session_zone(seg) or "").lower() != "task_linked":
            continue
        dur = _safe_float(seg.get("duration_minutes"), 0.0)
        if dur <= 0:
            continue
        tid = str(seg.get("task_id") or "").strip()
        tname = str(seg.get("task_name") or "").strip()
        if tid:
            minutes_by_task[tid] += dur
            if tname:
                segment_task_name_by_id[tid] = tname
        elif tname:
            pseudo = f"name::{tname}"
            minutes_by_task[pseudo] += dur
            segment_task_name_by_id[pseudo] = tname

    # Meetings summary from segments (work-hours only)
    meetings_by_name: dict[str, float] = defaultdict(float)
    for seg in work_segments:
        if str(_session_zone(seg) or "").lower() != "meeting":
            continue
        dur = _safe_float(seg.get("duration_minutes"), 0.0)
        if dur <= 0:
            continue
        nm = str(seg.get("task_name") or seg.get("title") or "").strip() or "Meeting"
        meetings_by_name[nm] += dur
    meetings_out = [
        {"name": name, "minutes": int(round(mins))}
        for name, mins in sorted(meetings_by_name.items(), key=lambda kv: (-kv[1], kv[0].lower()))
        if mins > 0
    ]

    session_count = 0
    for s in sessions:
        if not isinstance(s, dict):
            continue
        session_count += 1
        dur = _safe_float(s.get("duration_min"), default=0.0)
        zone = str(_extract_zone(s) or "").lower().strip()
        tid = _extract_task_id(s)
        if zone in {"untracked_work", "untracked"}:
            start_dt = _parse_iso_dt(str(s.get("start") or "")) if s.get("start") else None
            hm = _to_ist_hm(start_dt)

            urls = [str(u) for u in (s.get("urls") or []) if u and "APP_CONTEXT:" not in str(u)]
            domains = [d for d in (get_domain(u) for u in urls) if d]
            dom = (Counter(domains).most_common(1)[0][0] if domains else "") or ""

            apps = [str(a) for a in (s.get("apps") or []) if str(a).strip()]
            titles = [str(t) for t in (s.get("titles") or []) if str(t).strip()]

            ai_note = ""
            client = _get_vertex_client()
            if client is not None:
                prompt = f"""
A work session was flagged as untracked.
Duration: {int(round(dur))} minutes
Apps used: {apps}
URLs visited: {urls}
Titles: {titles}

In one short phrase (max 8 words), describe what the person was doing.
Example: "Personal messaging on WhatsApp"
Example: "General news browsing"
Example: "YouTube entertainment"
Reply with ONLY the phrase.
""".strip()
                try:
                    resp = client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=prompt,
                        config=types.GenerateContentConfig(temperature=0.1),
                    )
                    ai_note = (resp.text or "").strip().strip('"').strip()
                except Exception:
                    ai_note = ""

            untracked.append(
                {
                    "start": f"{hm} IST" if hm else "",
                    "minutes": int(round(dur)),
                    "dominant_domain": dom,
                    "ai_note": ai_note,
                }
            )

        if tid and zone == "task_linked":
            c = _extract_session_confidence(s)
            if c is not None:
                confs_by_task[tid].append(c)
                if c < 0.70:
                    low_conf_sessions.append(
                        {
                            "session_id": s.get("session_id"),
                            "start": s.get("start"),
                            "end": s.get("end"),
                            "zone": _extract_zone(s),
                            "clickup_task_name": _extract_task_name(s),
                            "confidence": c,
                        }
                    )

    # Fetch ClickUp task details
    cu = ClickUpClient()
    task_details_cache: dict[str, dict] = {}

    def get_task_detail(task_id: str) -> dict:
        if str(task_id).startswith("name::"):
            nm = str(task_id).split("name::", 1)[1].strip()
            return {"id": task_id, "name": nm}
        if task_id in task_details_cache:
            return task_details_cache[task_id]
        try:
            d = cu.get_task(task_id, include_subtasks=False) or {}
        except Exception as e:
            d = {"id": task_id, "error": str(e)}
        if not isinstance(d, dict):
            d = {"id": task_id}
        task_details_cache[task_id] = d
        return d

    def get_parent_name(task_detail: dict) -> str | None:
        parent = task_detail.get("parent")
        if not parent:
            return None
        parent_id = None
        if isinstance(parent, dict) and parent.get("id"):
            parent_id = parent.get("id")
        elif isinstance(parent, (str, int)):
            parent_id = parent
        if parent_id is None:
            return None
        pd = get_task_detail(str(parent_id))
        nm = pd.get("name")
        return str(nm).strip() if nm else None

    today_dt = _parse_iso_dt(f"{summary_date}T00:00:00+05:30") or datetime.now(IST)

    tasks_out: list[dict] = []
    for tid, mins in sorted(minutes_by_task.items(), key=lambda kv: (-kv[1], kv[0])):
        detail = get_task_detail(tid)
        raw_clickup_name = str(detail.get("name") or "").strip() or segment_task_name_by_id.get(str(tid), "").strip() or str(
            _extract_task_name(next((s for s in sessions if _extract_task_id(s) == tid), {})) or ""
        ).strip()
        display_name, em_dash_parent = _split_em_dash_display(raw_clickup_name)
        name = (display_name or raw_clickup_name or tid).strip()
        parent_name = get_parent_name(detail)

        est_ms = detail.get("time_estimate")
        est_min = None
        if est_ms not in (None, "", 0, "0"):
            try:
                est_min = max(0.0, float(est_ms) / 1000.0 / 60.0)
            except Exception:
                est_min = None

        due_epoch_ms = detail.get("due_date")
        due: date | None = None
        if due_epoch_ms not in (None, "", "0", 0):
            try:
                due = date.fromtimestamp(int(float(due_epoch_ms)) / 1000)
            except Exception:
                due = None

        status_obj = detail.get("status") if isinstance(detail.get("status"), dict) else {}
        status = str(status_obj.get("status") or status_obj.get("name") or "").strip().lower()

        if due_epoch_ms and due is not None:
            is_overdue = due < date.today() and status != "closed"
        else:
            is_overdue = False

        days_overdue = 0
        if is_overdue and due is not None:
            days_overdue = (date.today() - due).days

        pct_of_est = None
        if est_min and est_min > 0:
            pct_of_est = round((mins / est_min) * 100.0, 1)

        conf_list = confs_by_task.get(tid) or []
        avg_conf = round(mean(conf_list), 3) if conf_list else None

        if skip_ai:
            # Reuse stored category (if any) to avoid model calls for posting.
            skill_category = (
                existing_skill_by_id.get(str(tid))
                or existing_skill_by_name.get(str(name).strip().lower())
                or "general"
            )
        else:
            skill_category = categorise_task(name, parent_name or em_dash_parent)
        segment_aliases = _segment_match_aliases(raw_clickup_name, name, em_dash_parent)

        tasks_out.append(
            {
                "task_id": tid,
                "name": name or tid,
                "parent_name": parent_name,
                "task_em_dash_parent": em_dash_parent,
                "segment_match_names": segment_aliases,
                "status": status or None,
                "time_estimate_minutes": est_min,
                "due_date": due.isoformat() if due is not None else None,
                "is_overdue": is_overdue,
                "days_overdue": days_overdue,
                "today_minutes": round(mins, 1),
                "percent_of_estimate": pct_of_est,
                "avg_confidence": avg_conf,
                "skill_category": skill_category,
                "tools": _build_tools_for_task_name(work_segments, segment_aliases, cap_total_minutes=mins),
            }
        )

    tasks_list = tasks_out
    performance_signals = {
        "tasks_completed_today": [
            {
                "task_id": str(t.get("task_id") or ""),
                "name": str(t.get("name") or ""),
                "today_minutes": float(t.get("today_minutes") or 0.0),
                "skill_category": str(t.get("skill_category") or "general"),
            }
            for t in tasks_list
            if (t.get("status") or "") in ("closed", "complete")
        ],
        "tasks_overdue": [
            {
                "task_id": str(t.get("task_id") or ""),
                "name": str(t.get("name") or ""),
                "days_overdue": int(t.get("days_overdue") or 0),
                "due_date": t.get("due_date"),
                "today_minutes": float(t.get("today_minutes") or 0.0),
            }
            for t in tasks_list
            if bool(t.get("is_overdue"))
        ],
        "tasks_over_estimate": [
            {
                "task_id": str(t.get("task_id") or ""),
                "name": str(t.get("name") or ""),
                "time_estimate_minutes": float(t.get("time_estimate_minutes") or 0.0),
                "today_minutes": float(t.get("today_minutes") or 0.0),
                "percent_of_estimate": float(t.get("percent_of_estimate") or 0.0),
            }
            for t in tasks_list
            if t.get("percent_of_estimate") is not None and float(t.get("percent_of_estimate") or 0.0) > 100
        ],
        "tasks_within_estimate": [
            {
                "task_id": str(t.get("task_id") or ""),
                "name": str(t.get("name") or ""),
                "time_estimate_minutes": float(t.get("time_estimate_minutes") or 0.0),
                "today_minutes": float(t.get("today_minutes") or 0.0),
                "percent_of_estimate": float(t.get("percent_of_estimate") or 0.0),
            }
            for t in tasks_list
            if t.get("percent_of_estimate") is not None and float(t.get("percent_of_estimate") or 0.0) <= 100
        ],
    }

    summary = {
        "date": summary_date,
        "user": user_email,
        "productivity": prod,
        "computed": {
            "tracked_minutes": round(tracked_minutes, 1),
            "deep_work_minutes": round(deep_work_minutes, 1),
        },
        "session_count": int(session_count),
        "tasks": tasks_out,
        "performance_signals": performance_signals,
        "meetings": meetings_out,
        "untracked": untracked,
        "low_confidence_sessions": low_conf_sessions,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    out_path = out_dir / f"eod_{summary_date}.json"
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def format_eod_clickup_message(summary: dict) -> str:
    def _fmt_minutes_compact(mins: float | int | None) -> str:
        m = int(round(_safe_float(mins, 0.0)))
        if m <= 0:
            return "0m"
        h = m // 60
        r = m % 60
        if h <= 0:
            return f"{m}m"
        if r == 0:
            return f"{h}h"
        return f"{h}h {r}m"

    def _tools_for_task_row(t: dict, seg_rows: list[dict]) -> list[dict]:
        if "tools" in t:
            raw = t.get("tools")
            if isinstance(raw, list):
                return [x for x in raw if isinstance(x, dict)]
            return []
        aliases = t.get("segment_match_names")
        if isinstance(aliases, list) and aliases:
            match_list = [str(x) for x in aliases if str(x).strip()]
        else:
            match_list = [(t.get("name") or "").strip()]
        cap = _safe_float(t.get("today_minutes"), 0.0)
        return _build_tools_for_task_name(seg_rows, match_list, cap_total_minutes=cap)

    def _display_tool_hint(tool: dict) -> str:
        th = _strip_title_suffixes(str(tool.get("title_hint") or "")).strip()
        if th:
            return th
        return str(tool.get("app") or "").strip() or "?"

    def _fmt_task_block(t: dict, seg_rows: list[dict]) -> str | None:
        name = (t.get("name") or "").strip()
        if not name:
            return None
        mins = _safe_float(t.get("today_minutes"), 0.0)
        if mins <= 0:
            return None

        em_parent = (t.get("task_em_dash_parent") or "").strip()
        title_display = f"{name} ({em_parent})" if em_parent else name

        est_min = t.get("time_estimate_minutes")
        if est_min:
            est_str = _fmt_minutes_compact(est_min)
            head = f"• {title_display} — {_fmt_minutes_compact(mins)} (of {est_str} estimate)"
        else:
            head = f"• {title_display} — {_fmt_minutes_compact(mins)}"

        tools = _tools_for_task_row(t, seg_rows)
        lines = [head]
        if len(tools) >= 2:
            bits = [f"{_display_tool_hint(tool)} ({_fmt_minutes_compact(tool.get('minutes'))})" for tool in tools[:3]]
            lines.append("    via " + " · ".join(bits))
        return "\n".join(lines)

    date_str = str(summary.get("date") or "").strip() or None
    date_for_segs = str(summary.get("date") or "").strip()
    segment_rows_fmt = _load_segment_rows_for_date(date_for_segs) if date_for_segs else []

    tasks = list(summary.get("tasks") or [])
    tasks_sorted = sorted(tasks, key=lambda t: _safe_float((t or {}).get("today_minutes"), 0.0), reverse=True)
    task_lines = [line for line in (_fmt_task_block(t or {}, segment_rows_fmt) for t in tasks_sorted) if line]

    # "Tracked" should not depend on ClickUp task IDs being present.
    tracked_minutes = _safe_float((summary.get("computed") or {}).get("tracked_minutes"), 0.0)
    if tracked_minutes <= 0.0:
        tracked_minutes = sum(_safe_float((t or {}).get("today_minutes"), 0.0) for t in tasks)
    deep_work_minutes = _safe_float((summary.get("computed") or {}).get("deep_work_minutes"), 0.0)
    session_count = _safe_int(summary.get("session_count"), 0)
    prod = summary.get("productivity") or {}
    active_pct = _safe_float(prod.get("active_pct"), 0.0)

    focus_line = None
    if tasks_sorted:
        top = tasks_sorted[0] or {}
        top_name = (top.get("name") or "").strip()
        top_m = _safe_float(top.get("today_minutes"), 0.0)
        if top_name and top_m > 0:
            focus_line = f"Focus: {top_name} ({_fmt_minutes_compact(top_m)})"

    base_message = "\n".join(
        [
            f"EOD — {date_str} (IST)" if date_str else "EOD (IST)",
            f"Tracked: {_fmt_minutes_compact(tracked_minutes)} | Deep work: {_fmt_minutes_compact(deep_work_minutes)} | "
            f"Sessions: {session_count} | Active: {active_pct:.0f}%",
            "Worked on:",
            *(
                task_lines[:12]
                if task_lines
                else [
                    "- (No task-linked work captured today)",
                ]
            ),
            *([""] if focus_line else []),
            *(["" + focus_line] if focus_line else []),
        ]
    ).strip()

    performance_signals = summary.get("performance_signals") or {}
    overdue = performance_signals.get("tasks_overdue", 0)
    if isinstance(overdue, list):
        overdue_names = [str((t or {}).get("name") or "") for t in overdue if isinstance(t, dict)]
        overdue_count = len(overdue)
    else:
        overdue_names = [str(n) for n in (performance_signals.get("overdue_task_names") or []) if n]
        try:
            overdue_count = int(overdue)
        except Exception:
            overdue_count = 0
    if overdue_count > 0:
        base_message += "\n⚠️ Overdue: " + ", ".join(n for n in overdue_names if n)

    # If Vertex/Gemini is available, let it rewrite the deterministic base message.
    # This keeps local `--write-out` useful even without cloud credentials.
    client = _get_vertex_client()
    if client is None:
        return base_message

    prompt = f"""
You are writing an EOD report for a design agency called Value at Void.
This will be posted in their ClickUp chat channel.

Here is a structured work summary (JSON):
{json.dumps(summary, indent=2)}

Here is a baseline message you may improve, but keep the same facts:
{base_message}

Requirements:
- Keep it clean and professional.
- Be concise (max 20 lines).
- Use emojis sparingly (0–2 total).
- Use IST.
- Time format must be compact: "2h", "2h 5m", or "15m" (never "2h 0m").
- Never mention confidence scores or internal mapping methods.
- Never show task IDs.
- End with one single-line focus summary.
""".strip()

    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.2),
        )
        text = (resp.text or "").strip()
        return text or base_message
    except Exception:
        return base_message


def post_to_clickup_channel(message: str, channel_id: str) -> bool:
    import requests

    token = (os.getenv("CLICKUP_TOKEN", "") or "").strip()
    if not token:
        return False
    # ClickUp Chat API is v3 workspace-scoped.
    workspace_id = (os.getenv("CLICKUP_TEAM_ID", "") or "").strip()
    if not workspace_id:
        return False
    url = f"https://api.clickup.com/api/v3/workspaces/{workspace_id}/chat/channels/{channel_id}/messages"
    try:
        r = requests.post(
            url,
            headers={"Authorization": token},
            json={"content": message},
            timeout=30,
        )
        return r.status_code in (200, 201)
    except Exception:
        return False

