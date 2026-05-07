from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pipeline.eod.skill_profile import build_skill_profile


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _is_incomplete_week(profile: dict[str, Any]) -> bool:
    """
    Heuristic: a weekly profile with only one category at ~100% is likely incomplete data
    (missing days / missing EOD inputs).
    """
    sb = profile.get("skill_breakdown") or {}
    if not isinstance(sb, dict) or not sb:
        return True
    nonzero = []
    for k, v in sb.items():
        if not isinstance(v, dict):
            continue
        try:
            pct = float(v.get("percentage") or 0.0)
        except Exception:
            pct = 0.0
        if pct > 0:
            nonzero.append((str(k), pct))
    if len(nonzero) != 1:
        return False
    return nonzero[0][1] >= 99.9


def _skill_pct_map(profile: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    sb = profile.get("skill_breakdown") or {}
    if not isinstance(sb, dict):
        return out
    for k, v in sb.items():
        if not isinstance(v, dict):
            continue
        try:
            out[str(k)] = float(v.get("percentage") or 0.0)
        except Exception:
            out[str(k)] = 0.0
    return out


def _direction(delta: float) -> str:
    if delta > 0.05:
        return "up"
    if delta < -0.05:
        return "down"
    return "flat"


def _insight_from_trend(trend: dict[str, dict]) -> str:
    if not trend:
        return "Not enough data for trend — check back next week"
    # Pick top 2 absolute changes for a simple summary.
    changes = sorted(
        ((k, float(v.get("change") or 0.0)) for k, v in trend.items()),
        key=lambda kv: abs(kv[1]),
        reverse=True,
    )
    if not changes:
        return "Not enough data for trend — check back next week"
    parts: list[str] = []
    for k, ch in changes[:2]:
        if abs(ch) < 0.5:
            continue
        dir_word = "increased" if ch > 0 else "reduced"
        parts.append(f"{k.capitalize()} focus {dir_word} by {abs(ch):.1f}pp")
    return ". ".join(parts) + ("." if parts else "")


def build_performance_trend(
    employee_email: str,
    end_date: str,
    weeks: int = 4,
    out_dir: Path = Path("out"),
) -> dict:
    """
    Compare skill profiles across multiple weeks.

    For each week ending on:
      end_date (this week)
      end_date - 7 days (last week)
      end_date - 14 days (2 weeks ago)
      etc.

    Loads or generates skill profile for each week.
    Compares skill_breakdown percentages.

    Writes: out/trend_YYYY-MM-DD.json
    """
    end_d = date.fromisoformat(end_date)
    out_dir = Path(out_dir)

    weekly_profiles: list[dict[str, Any]] = []
    for w in range(max(int(weeks), 1)):
        week_end = (end_d - timedelta(days=7 * w)).isoformat()
        path = out_dir / f"skill_profile_{week_end}.json"
        prof = _read_json(path)
        if prof is None:
            # Generate weekly profile; it writes to default OUTPUT_DIR via build_skill_profile's out_dir,
            # but we return the dict here and also persist the trend report in out_dir.
            try:
                prof = build_skill_profile(week_end, window="weekly", employee=employee_email)
            except Exception:
                prof = None
        if isinstance(prof, dict):
            weekly_profiles.append(prof)

    weekly_data: list[dict[str, Any]] = []
    for p in weekly_profiles:
        weekly_data.append(
            {
                "week_ending": str(p.get("period") or "").split(" to ")[-1].strip() or end_date,
                "top_skill": p.get("top_skill") or "general",
                "focus_score": float(p.get("focus_score") or 0.0),
                "incomplete_data": _is_incomplete_week(p),
            }
        )

    weeks_analyzed = len(weekly_profiles)
    trend: dict[str, dict] = {}

    if weeks_analyzed >= 2:
        current = weekly_profiles[0]
        previous = weekly_profiles[1]
        cur_map = _skill_pct_map(current)
        prev_map = _skill_pct_map(previous)
        skills = sorted(set(cur_map.keys()) | set(prev_map.keys()))
        for s in skills:
            cur = float(cur_map.get(s, 0.0))
            prev = float(prev_map.get(s, 0.0))
            ch = round(cur - prev, 1)
            trend[s] = {
                "current": round(cur, 1),
                "previous": round(prev, 1),
                "change": ch,
                "direction": _direction(ch),
            }

    # Data quality gate: if any previous week looks incomplete, warn and mark reliability.
    complete_weeks = [w for w in weekly_data if not w.get("incomplete_data")]
    weeks_reliable = len(complete_weeks)
    previous_incomplete = any(w.get("incomplete_data") for w in weekly_data[1:])

    if weeks_analyzed >= 2 and previous_incomplete:
        insight = (
            f"Trend data building up — {weeks_analyzed} weeks collected. "
            "Full trends available after 2 complete weeks."
        )
    else:
        insight = _insight_from_trend(trend)

    out = {
        "employee": employee_email,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weeks_analyzed": weeks_analyzed,
        "weeks_reliable": weeks_reliable,
        "trend": trend if weeks_analyzed >= 2 else {},
        "insight": insight,
        "weekly_data": weekly_data,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"trend_{end_date}.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out

