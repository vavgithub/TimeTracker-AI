from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from config import OUTPUT_DIR


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_segments(segments: list[dict], date_str: str, out_dir: Path) -> None:
    """
    Write `out/segments_YYYY-MM-DD.json` with dedupe keyed by segment id (latest wins).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"segments_{date_str}.json"

    existing_obj = None
    if out_path.exists():
        try:
            existing_obj = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            existing_obj = None

    by_id: dict[str, dict] = {}
    if isinstance(existing_obj, dict):
        for s in existing_obj.get("segments") or []:
            if isinstance(s, dict) and s.get("id"):
                by_id[str(s["id"])] = s

    for s in segments or []:
        if not isinstance(s, dict):
            continue
        sid = s.get("id")
        if not sid:
            continue
        by_id[str(sid)] = s

    merged_segments = [by_id[k] for k in sorted(by_id.keys(), key=lambda x: by_id[x].get("start") or "")]

    by_task: dict[str, float] = {}
    total_meeting = 0.0
    total_task = 0.0
    total_untracked = 0.0

    for s in merged_segments:
        zone = str(s.get("zone") or "").lower()
        dur = float(s.get("duration_minutes") or 0.0)
        if dur <= 0:
            continue
        if zone == "meeting":
            label = str(s.get("task_name") or "Meeting")
            by_task[label] = by_task.get(label, 0.0) + dur
            total_meeting += dur
        elif zone == "untracked_work":
            label = "Untracked"
            by_task[label] = by_task.get(label, 0.0) + dur
            total_untracked += dur
        else:
            label = str(s.get("task_name") or "Task")
            by_task[label] = by_task.get(label, 0.0) + dur
            total_task += dur

    obj = {
        "date": date_str,
        "generated_at": _now_iso(),
        "segment_count": len(merged_segments),
        "segments": merged_segments,
        "daily_summary": {
            "by_task": {k: round(v, 3) for k, v in sorted(by_task.items(), key=lambda kv: (-kv[1], kv[0]))},
            "total_meeting_minutes": round(total_meeting, 3),
            "total_task_minutes": round(total_task, 3),
            "total_untracked_minutes": round(total_untracked, 3),
        },
    }

    out_path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Written: {OUTPUT_DIR / f'segments_{date_str}.json'} ({len(merged_segments)} segments)")
