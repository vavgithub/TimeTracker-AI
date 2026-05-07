from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import OUTPUT_DIR
from pipeline.mapping.task_mapper import IST, _clickup_tasks_prompt_block, _display_task_name

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]


def _ensure_out_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def build_daily_context(segments: list[dict]) -> dict[str, float]:
    """
    Aggregate time per unique app+title combination.
    Returns a dict sorted by time desc. Only includes >= 1 minute combos.
    """
    totals: dict[str, float] = defaultdict(float)
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        app = str(seg.get("app") or "").strip()
        title = str(seg.get("title") or "").strip()
        if not app or not title:
            continue
        try:
            mins = float(seg.get("duration_minutes") or 0.0)
        except Exception:
            mins = 0.0
        if mins <= 0:
            continue
        key = f"{app} — {title}"
        totals[key] += mins

    filtered = {k: v for k, v in totals.items() if v >= 1.0}
    return dict(sorted(filtered.items(), key=lambda x: x[1], reverse=True))


def _hash_context(context: dict[str, float]) -> str:
    payload = json.dumps(context, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:16]


def _cache_path(date_str: str) -> Path:
    return _ensure_out_dir() / f"context_cache_{date_str}.json"


def _load_cache(date_str: str) -> dict:
    obj = _read_json(_cache_path(date_str))
    return obj if isinstance(obj, dict) else {}


def _save_cache(date_str: str, cache: dict) -> None:
    _write_json(_cache_path(date_str), cache)


def _calendar_block(calendar_events: list[dict]) -> str:
    if not calendar_events:
        return "  (none)"
    lines: list[str] = []
    for e in calendar_events or []:
        name = str(e.get("name") or "").strip() or "Meeting"
        try:
            s_ts = float(e.get("start_ts") or 0)
            e_ts = float(e.get("end_ts") or 0)
        except Exception:
            continue
        if not s_ts or not e_ts or e_ts <= s_ts:
            continue
        s = datetime.fromtimestamp(s_ts, tz=IST).strftime("%H:%M")
        en = datetime.fromtimestamp(e_ts, tz=IST).strftime("%H:%M")
        lines.append(f"  - {name} ({s}–{en} IST)")
    return "\n".join(lines) if lines else "  (none)"


def map_daily_context(
    context: dict[str, float],
    tasks: list[dict],
    task_by_id: dict[str, dict],
    calendar_events: list[dict],
    employee_name: str,
    date_str: str,
) -> dict[str, dict]:
    """
    ONE AI call maps all app+title combinations to tasks/meetings/untracked.
    Returns mapping keyed by \"app — title\" string.
    """
    project = os.getenv("GCP_PROJECT_ID", "").strip()
    region = os.getenv("GCP_REGION", "us-central1").strip()
    if genai is None or types is None or not project:
        return {}

    context_lines = "\n".join(
        [f"  [{i+1}] {k}: {v:.0f}m" for i, (k, v) in enumerate(context.items())]
    )
    task_block = _clickup_tasks_prompt_block(tasks, task_by_id) if tasks else "  (no open tasks)"
    cal_block = _calendar_block(calendar_events)

    prompt = f"""
You are mapping a full day's computer activity to work tasks for {employee_name} at Value at Void (design agency).

DATE: {date_str}

CALENDAR EVENTS:
{cal_block}

CLICKUP TASKS (open/in-progress):
{task_block}

ACTIVITY SUMMARY (app — window title: minutes spent):
{context_lines}

Map EACH activity line to one of:
  - A ClickUp task ID from the list above
  - \"meeting:[name]\" for meetings/calls
  - \"untracked:[reason]\" for non-work activity

Reply ONLY with valid JSON, no markdown:
{{
  \"1\": {{"zone": "task_linked", "task_id": "86d2xxx", "task_name": "...", "confidence": 0.90}},
  \"2\": {{"zone": "meeting", \"task_id\": null, \"task_name\": \"Standup call\", \"confidence\": 0.95}},
  \"3\": {{"zone": \"untracked_work\", \"task_id\": null, \"task_name\": \"Personal browsing\", \"confidence\": 0.85}}
}}

Rules:
- Match most specific ClickUp subtask possible
- DEVELOPMENT FILES:
  If the segment shows a .js, .jsx, .ts, .tsx, .py, .css filename in an editor (Cursor, VSCode):
    → This is development work
    → Match to the most relevant open ClickUp task
    → Never mark as untracked_work
  hirehive-1 repository files → Geode project tasks
  time_tracker repository files → Time Tracker tasks
- CLAUDE AND AI TOOL CLASSIFICATION:
  When the employee uses Claude, ChatGPT, or other AI assistants, classify based on the CONVERSATION TITLE not the tool itself.
  Examples:
    'Time Tracker - Claude' → same category as Time Tracker task
    'segment_builder.py fixes - Claude' → development
    'Hiring interview questions - Claude' → hiring
    'Brand guidelines review - Claude' → branding
    'EOD Timesheet - Claude' → development
  The AI tool is just an instrument. The conversation topic is the actual work.
- Zoom/Meet + Meeting title = meeting
- SyncUp Call with X = meeting
- Calendar event overlap = meeting
- leaves.atvoid.com and zoho.com/people tabs are HR admin tools — classify as untracked_work unless the task is specifically HR related
- Entertainment/social = untracked_work
- NEVER return unknown
- confidence 0.50-0.98
""".strip()

    client = genai.Client(vertexai=True, project=project, location=region)
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0),
    )

    text = (resp.text or "").strip()
    text = re.sub(r"```json|```", "", text, flags=re.I).strip()
    mapping = json.loads(text) if text else {}
    if not isinstance(mapping, dict):
        return {}

    context_keys = list(context.keys())
    result: dict[str, dict] = {}
    for i, key in enumerate(context_keys):
        num = str(i + 1)
        row = mapping.get(num)
        if isinstance(row, dict):
            result[key] = row
    return result


def apply_mapping_to_segments(segments: list[dict], mapping: dict[str, dict], task_by_id: dict[str, dict]) -> list[dict]:
    mapped: list[dict] = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        key = f"{seg.get('app','')} — {seg.get('title','')}"
        classification = mapping.get(key) if isinstance(mapping, dict) else None
        if not isinstance(classification, dict):
            classification = {
                "zone": "untracked_work",
                "task_id": None,
                "task_name": "Unknown",
                "confidence": 0.50,
            }

        zone = str(classification.get("zone") or "untracked_work").lower().strip()
        if zone not in ("meeting", "task_linked", "untracked_work"):
            zone = "untracked_work"

        out = dict(seg)
        out["zone"] = zone
        out["task_id"] = classification.get("task_id")
        out["task_name"] = classification.get("task_name")
        try:
            out["confidence"] = float(classification.get("confidence") or 0.50)
        except Exception:
            out["confidence"] = 0.50
        out["method"] = "daily_context"
        out["reason"] = (classification.get("reason") or "").strip()

        if zone == "task_linked" and out.get("task_id") and str(out.get("task_id")).strip() in task_by_id:
            out["task_name"] = _display_task_name(task_by_id[str(out["task_id"]).strip()], task_by_id)

        # For meetings, show the meeting name as the segment title (improves timeline + cards).
        if zone == "meeting":
            tn = str(out.get("task_name") or "").strip()
            if tn:
                out["title"] = tn

        mapped.append(out)
    return mapped


def map_segments(
    segments: list[dict],
    tasks: list[dict],
    task_by_id: dict[str, dict],
    calendar_events: list[dict],
    employee_name: str,
    date_str: str,
) -> list[dict]:
    context = build_daily_context(segments)
    print(f"[segments] {len(segments or [])} segments → {len(context)} unique contexts")

    cache = _load_cache(date_str)
    context_hash = _hash_context(context)
    mapping: dict[str, dict]
    if context_hash in cache and isinstance(cache.get(context_hash), dict):
        print("[segments] using cached mapping")
        mapping = cache[context_hash]
    else:
        print(f"[segments] calling AI for {len(context)} contexts...")
        t0 = time.time()
        mapping = map_daily_context(context, tasks, task_by_id, calendar_events, employee_name, date_str)
        print(f"[segments] AI done in {time.time() - t0:.1f}s")
        cache[context_hash] = mapping
        _save_cache(date_str, cache)

    mapped = apply_mapping_to_segments(segments, mapping, task_by_id)
    mapped.sort(key=lambda x: str(x.get("start") or ""))
    return _group_segments_by_context(mapped)


def _parse_iso_dt(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _group_segments_by_context(segments: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for seg in segments:
        if not merged:
            merged.append(dict(seg))
            continue
        prev = merged[-1]
        same = (
            prev.get("zone") == seg.get("zone")
            and prev.get("task_id") == seg.get("task_id")
            and (prev.get("task_name") or "") == (seg.get("task_name") or "")
        )
        if same:
            prev_end = _parse_iso_dt(str(prev.get("end") or ""))
            seg_start = _parse_iso_dt(str(seg.get("start") or ""))
            seg_end = _parse_iso_dt(str(seg.get("end") or ""))
            if prev_end and seg_start and seg_end and seg_start >= prev_end - timedelta(seconds=2):
                prev["end"] = str(seg.get("end") or "")
                prev["duration_minutes"] = round(float(prev.get("duration_minutes") or 0.0) + float(seg.get("duration_minutes") or 0.0), 3)
                # Keep first id for stability
                continue
        merged.append(dict(seg))
    return merged
