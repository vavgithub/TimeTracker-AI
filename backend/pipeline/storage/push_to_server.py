"""
POST trimmed pipeline payloads to an admin server (optional).

Env:
  ADMIN_SERVER_URL — base URL, e.g. https://portal.example.com
  PUSH_API_KEY — sent as header x-api-key

Missing env, missing JSON files, or network errors: no-op (silent).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

import requests

from config import USER_EMAIL


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _admin_base() -> str | None:
    raw = (os.getenv("ADMIN_SERVER_URL") or "").strip().rstrip("/")
    return raw or None


def _api_key() -> str | None:
    raw = (os.getenv("PUSH_API_KEY") or "").strip()
    return raw or None


def _post_json(path_suffix: str, body: dict[str, Any]) -> None:
    base = _admin_base()
    key = _api_key()
    if not base or not key:
        return
    url = f"{base}{path_suffix}" if path_suffix.startswith("/") else f"{base}/{path_suffix}"
    headers = {"x-admin-api-key": key, "Content-Type": "application/json"}

    for attempt in range(1, 4):
        try:
            print(f"[debug] posting to: {url}")
            print(f"[debug] api key: {(key[:10] + '...') if len(key) > 10 else (key + '...')}")
            resp = requests.post(url, json=body, headers=headers, timeout=30)
            if resp.status_code < 400:
                print(f"[push] ✓ {path_suffix} → {resp.status_code}")
                return
            elif resp.status_code < 500:
                print(f"[push] ✗ {path_suffix} client error {resp.status_code}: {resp.text[:200]}")
                return  # do not retry 4xx
            else:
                print(f"[push] attempt {attempt}/3 server error {resp.status_code}: {resp.text[:100]}")
        except Exception as exc:
            print(f"[push] attempt {attempt}/3 exception: {exc}")

        if attempt < 3:
            import time

            time.sleep(5)

    print(f"[push] ✗ failed after 3 attempts: {url}")


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


def push_daily_summary(date_str: str, out_dir: Path) -> None:
    """POST /api/v1/pipeline/daily-summary — trimmed productivity + deep_work from eod."""
    try:
        prod_path = out_dir / f"productivity_{date_str}.json"
        eod_path = out_dir / f"eod_{date_str}.json"
        prod = _read_json(prod_path)
        if not isinstance(prod, Mapping):
            return
        eod = _read_json(eod_path)
        computed: Mapping[str, Any] = {}
        if isinstance(eod, Mapping):
            c = eod.get("computed")
            if isinstance(c, Mapping):
                computed = c

        user = str(
            (eod.get("user") if isinstance(eod, Mapping) else None)
            or prod.get("user")
            or USER_EMAIL
        ).strip()
        print(f"[push] daily-summary → {date_str} for {user}")
        deep_work = _as_float(computed.get("deep_work_minutes"), 0.0)

        body = {
            "user_email": user,
            "date": str(prod.get("date") or date_str),
            "active_minutes": round(_as_float(prod.get("active_minutes")), 1),
            "idle_minutes": round(_as_float(prod.get("idle_minutes")), 1),
            "activity_rate": round(_as_float(prod.get("activity_rate")), 1),
            "deep_work_minutes": round(deep_work, 1),
            "meeting_minutes": round(_as_float(prod.get("meeting_minutes")), 1),
            "keystrokes": _as_int(prod.get("keystrokes"), 0),
            "mouse_clicks": _as_int(prod.get("mouse_clicks"), 0),
        }
        _post_json("/api/v1/pipeline/daily-summary", body)
    except Exception:
        return


def push_eod_report(date_str: str, out_dir: Path) -> None:
    """POST /api/v1/pipeline/eod-report — trimmed tasks, meetings, performance_signals, untracked + narrative."""
    from pipeline.eod.summary_writer import format_eod_clickup_message

    try:
        path = out_dir / f"eod_{date_str}.json"
        full = _read_json(path)
        if not isinstance(full, Mapping):
            return

        user = str(full.get("user") or USER_EMAIL).strip()
        print(f"[push] eod-report → {date_str} for {user}")
        narrative = ""
        try:
            narrative = format_eod_clickup_message(dict(full))
        except Exception:
            narrative = ""

        tasks_out: list[dict[str, Any]] = []
        for t in full.get("tasks") or []:
            if not isinstance(t, Mapping):
                continue
            tools_raw = t.get("tools") if isinstance(t.get("tools"), list) else []
            tools_out: list[dict[str, Any]] = []
            for tool in tools_raw:
                if not isinstance(tool, Mapping):
                    continue
                app = str(tool.get("app") or "").strip()
                if not app:
                    continue
                tools_out.append(
                    {
                        "app": app,
                        "minutes": int(round(_as_float(tool.get("minutes"), 0.0))),
                    }
                )
            est = t.get("time_estimate_minutes")
            est_out: int | float | None
            if est is None:
                est_out = None
            else:
                ef = _as_float(est, 0.0)
                est_out = int(ef) if ef == int(ef) else round(ef, 1)

            tasks_out.append(
                {
                    "task_id": str(t.get("task_id") or ""),
                    "name": str(t.get("name") or ""),
                    "today_minutes": round(_as_float(t.get("today_minutes")), 1),
                    "skill_category": str(t.get("skill_category") or "general"),
                    "is_overdue": bool(t.get("is_overdue")),
                    "days_overdue": _as_int(t.get("days_overdue"), 0),
                    "due_date": t.get("due_date"),
                    "time_estimate_minutes": est_out,
                    "percent_of_estimate": t.get("percent_of_estimate"),
                    "tools": tools_out,
                }
            )

        meetings_out: list[dict[str, Any]] = []
        for m in full.get("meetings") or []:
            if not isinstance(m, Mapping):
                continue
            meetings_out.append(
                {
                    "name": str(m.get("name") or ""),
                    "minutes": _as_int(m.get("minutes"), 0),
                }
            )

        ps_in = full.get("performance_signals") if isinstance(full.get("performance_signals"), Mapping) else {}
        performance_signals = {
            "tasks_completed_today": (
                ps_in.get("tasks_completed_today")
                if isinstance(ps_in.get("tasks_completed_today"), list)
                else _as_int(ps_in.get("tasks_completed_today"), 0)
            ),
            "tasks_overdue": (
                ps_in.get("tasks_overdue")
                if isinstance(ps_in.get("tasks_overdue"), list)
                else _as_int(ps_in.get("tasks_overdue"), 0)
            ),
            "tasks_within_estimate": (
                ps_in.get("tasks_within_estimate")
                if isinstance(ps_in.get("tasks_within_estimate"), list)
                else _as_int(ps_in.get("tasks_within_estimate"), 0)
            ),
            "tasks_over_estimate": (
                ps_in.get("tasks_over_estimate")
                if isinstance(ps_in.get("tasks_over_estimate"), list)
                else _as_int(ps_in.get("tasks_over_estimate"), 0)
            ),
        }

        untracked_out: list[dict[str, Any]] = []
        for u in full.get("untracked") or []:
            if not isinstance(u, Mapping):
                continue
            untracked_out.append(
                {
                    "minutes": _as_int(u.get("minutes"), 0),
                    "dominant_domain": str(u.get("dominant_domain") or ""),
                }
            )

        body = {
            "user_email": user,
            "date": str(full.get("date") or date_str),
            "narrative": narrative,
            "tasks": tasks_out,
            "meetings": meetings_out,
            "performance_signals": performance_signals,
            "untracked": untracked_out,
        }
        _post_json("/api/v1/pipeline/eod-report", body)
    except Exception:
        return


def push_skill_profile(date_str: str, out_dir: Path) -> None:
    """POST /api/v1/pipeline/skill-profile — date_str is week-ending ISO date; file skill_profile_{date_str}.json."""
    try:
        path = out_dir / f"skill_profile_{date_str}.json"
        raw = _read_json(path)
        if not isinstance(raw, Mapping):
            return

        print(f"[push] skill-profile → {date_str}")

        sb_in = raw.get("skill_breakdown")
        skill_breakdown: dict[str, dict[str, Any]] = {}
        if isinstance(sb_in, dict):
            for skill, block in sb_in.items():
                if not isinstance(block, Mapping):
                    continue
                skill_breakdown[str(skill)] = {
                    "minutes": _as_int(block.get("minutes"), 0),
                    "percentage": round(_as_float(block.get("percentage")), 1),
                }

        _consistency_raw = raw.get("consistency")
        _consistency: float | None = None
        if _consistency_raw is not None:
            try:
                _consistency = round(float(_consistency_raw), 2)
            except (TypeError, ValueError):
                _consistency = None

        body = {
            "user_email": str(raw.get("employee") or USER_EMAIL).strip(),
            "week_ending": date_str,
            "skill_breakdown": skill_breakdown,
            "top_skill": str(raw.get("top_skill") or ""),
            "focus_score": round(_as_float(raw.get("focus_score")), 1),
            "consistency": _consistency,
        }
        _post_json("/api/v1/pipeline/skill-profile", body)
    except Exception:
        return
