from pathlib import Path
from datetime import date
from collections import Counter, defaultdict
from urllib.parse import urlparse

import argparse
import datetime as _dt
import os

import config  # noqa: F401 — loads repo-root `.env` and path constants

from integrations.aw.client import pull
from integrations.aw.segment_builder import build_segments_with_web
from integrations.aw.session_builder import build_app_breakdown, build_sessions, split_sessions_at_meeting_boundaries
from config import (
    AFK_BUCKET,
    CLICKUP_CALLS_LIST_ID,
    CLICKUP_TEAM_ID,
    CLICKUP_USER_EMAIL,
    OUTPUT_DIR,
    USER_EMAIL,
    WEB_BUCKETS,
    WINDOW_BUCKET,
)
from integrations.clickup.client import ClickUpClient

try:
    from integrations.gcal.client import process_sessions_with_calendar
except ImportError:
    process_sessions_with_calendar = None  # type: ignore[misc, assignment]

from pipeline.mapping.meeting_mapper import process_meeting_sessions
from pipeline.mapping.segment_mapper import map_segments
from pipeline.mapping.task_mapper import map_sessions_to_tasks
from pipeline.metrics.productivity import (
    aggregate_daily_input_from_events,
    fetch_input_and_afk_events,
    get_session_input_summary,
)
from pipeline.storage.push_to_server import push_daily_summary, push_eod_report, push_skill_profile
from pipeline.storage.segment_writer import write_segments
from pipeline.storage.writer import write_daily_summary, write_sessions
from pipeline.eod.skill_profile import write_skill_profile
from pipeline.eod.summary_writer import generate_eod_summary, format_eod_clickup_message, post_to_clickup_channel
from pipeline.metrics.trend_detection import build_performance_trend
from pipeline.metrics.weekly_insight import format_weekly_clickup_message, generate_weekly_insight

# Skip noisy / low-signal hosts for domain summary (duration still counted in total_web_seconds from raw events).
NOISE_DOMAIN_SUBSTRINGS = ("newtab", "localhost", "127.0.0.1", "::1")

DOMAIN_ALIASES = {
    "us06web.zoom.us": "zoom.us",
    "us02web.zoom.us": "zoom.us",
    "mail.google.com": "gmail.com",
    "app.clickup.com": "clickup.com",
    "developer.clickup.com": "clickup.com",
    "docs.google.com": "google docs",
    "drive.google.com": "google drive",
    "console.cloud.google.com": "google cloud",
}


def _normalize_domain_for_summary(url: str | None, raw_host: str | None) -> str | None:
    if url is None and not (raw_host or "").strip():
        return None
    u = str(url or "").strip()
    if not u and not (raw_host or "").strip():
        return None
    ul = u.lower()
    if ul.startswith("chrome://") and "newtab" in ul:
        return None
    host = ""
    if u:
        try:
            parsed = urlparse(u if "://" in u else f"https://{u}")
            host = (parsed.netloc or "").lower().strip()
        except Exception:
            host = ""
    if not host:
        host = (raw_host or "").lower().strip()
    if not host or host == "unknown":
        return None
    if any(x in host for x in NOISE_DOMAIN_SUBSTRINGS):
        return None
    if host in DOMAIN_ALIASES:
        return DOMAIN_ALIASES[host]
    if host.endswith(".zoom.us") or host == "zoom.us":
        return "zoom.us"
    return host


def summarise_web_events(web_events: list) -> dict:
    """
    Aggregate ActivityWatch web bucket events by normalized domain label (duration-weighted).
    Filters noise (newtab/localhost) and collapses common aliases (Zoom, Google apps, ClickUp).
    """
    by_domain: Counter = Counter()
    raw_total_sec = 0.0
    for e in web_events or []:
        if not isinstance(e, dict):
            continue
        data = e.get("data") or {}
        url = data.get("url")
        dur = float(e.get("duration", 0) or 0)
        raw_total_sec += dur
        label = _normalize_domain_for_summary(url, None)
        if not label:
            continue
        by_domain[label] += dur

    top = by_domain.most_common(20)
    filtered_sec = sum(by_domain.values())
    return {
        "domains_top": [
            {"domain": d, "seconds": round(sec, 1), "minutes": round(sec / 60.0, 1)}
            for d, sec in top
        ],
        "unique_domains": len(by_domain),
        "total_web_seconds": round(raw_total_sec, 1),
        "filtered_web_seconds": round(filtered_sec, 1),
        "event_count": len(web_events or []),
    }


def _parse_args():
    p = argparse.ArgumentParser(description="ActivityWatch → silent task map + AFK productivity → out/*.json")
    p.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD (local calendar day)")
    p.add_argument(
        "--debug-clickup",
        action="store_true",
        help="Print ClickUp selection + time entry counts",
    )
    p.add_argument(
        "--write-out",
        action="store_true",
        help="Write out/sessions_*.json and out/daily_*.json",
    )
    p.add_argument(
        "--skill-profile",
        default="",
        metavar="YYYY-MM-DD",
        help="Aggregate EOD skill categories ending on this date; writes out/skill_profile_DATE.json (no AW pull)",
    )
    p.add_argument(
        "--skill-window",
        choices=("daily", "weekly", "monthly"),
        default="weekly",
        help="Window for --skill-profile: daily=1d, weekly=7d, monthly=30d (inclusive end date)",
    )
    p.add_argument(
        "--trend-report",
        default="",
        metavar="YYYY-MM-DD",
        help="Build weekly performance trend ending on this date; writes out/trend_DATE.json (no AW pull)",
    )
    p.add_argument(
        "--weekly-insight",
        default="",
        metavar="YYYY-MM-DD",
        help="Build weekly insight report ending on this date; writes out/weekly_insight_DATE.json and posts to ClickUp",
    )
    return p.parse_args()


def _guess_label_from_context(s: dict) -> str:
    # Deterministic “AI says …” label (no external model).
    apps = [str(a) for a in (s.get("apps") or []) if str(a).strip()]
    titles = [str(t) for t in (s.get("titles") or []) if str(t).strip()]
    urls = [str(u) for u in (s.get("urls") or []) if str(u).strip() and not str(u).startswith("APP_CONTEXT:")]

    # Prefer a strong title signal
    for t in titles:
        tl = t.lower()
        if any(x in tl for x in ("meeting", "call", "standup", "sync", "review")):
            return t
        if "oauth" in tl or "google auth" in tl or "cloud console" in tl:
            return "Google Cloud / OAuth setup"

    # Else prefer a meaningful domain
    for u in urls:
        d = _normalize_domain_for_summary(u, None)
        if d:
            return d

    # Else fall back to app (avoid raw APP_CONTEXT pseudo-urls)
    browserish = {"brave", "google-chrome", "chrome", "chromium", "firefox"}
    for a in apps:
        if a.lower() not in browserish:
            return a
    if titles:
        return titles[0][:80]
    return "Browser"


def _coalesce_micro_sessions(rows: list[dict]) -> list[dict]:
    """
    Merge tiny unknown/unmapped slots into neighbors to avoid empty/failed-looking gaps.
    """
    if not rows:
        return rows

    out: list[dict] = []
    for r in rows:
        out.append(r)

        # attempt merge with previous if this is tiny + low-signal
        if len(out) < 2:
            continue
        cur = out[-1]
        prev = out[-2]

        dur = float(cur.get("duration_min", 0) or 0)
        zone = str(cur.get("zone") or cur.get("ai_enrichment", {}).get("zone") or "").lower()
        name = (cur.get("clickup_task_name") or cur.get("ai_enrichment", {}).get("clickup_task_name") or "").strip()
        low_signal = (not (cur.get("titles") or [])) and (not (cur.get("urls") or []))

        if dur <= 3.0 and low_signal and (zone in ("unknown", "unclear", "")) and not name:
            # absorb into previous session timing + context
            prev["end"] = cur["end"]
            prev["duration_min"] = round(float(prev.get("duration_min", 0) or 0) + dur, 2)
            prev["duration_hours"] = round(float(prev.get("duration_hours", 0) or 0) + dur / 60.0, 2)
            # merge arrays
            prev["apps"] = list({*(prev.get("apps") or []), *(cur.get("apps") or [])})
            prev["titles"] = list({*(prev.get("titles") or []), *(cur.get("titles") or [])})
            prev["urls"] = list({*(prev.get("urls") or []), *(cur.get("urls") or [])})
            # prefer keeping prev enrichment; drop current
            out.pop()
    return out


def main():
    args = _parse_args()
    if getattr(args, "weekly_insight", None) and str(args.weekly_insight).strip():
        end_iso = str(args.weekly_insight).strip()
        employee = (CLICKUP_USER_EMAIL or USER_EMAIL or "").strip() or "unknown"
        out_dir = OUTPUT_DIR
        insight = generate_weekly_insight(employee, end_iso, out_dir=out_dir)
        msg = format_weekly_clickup_message(insight)
        print(msg)
        # Post to ClickUp EOD channel if configured
        channel_id = (os.getenv("EOD_CLICKUP_CHANNEL_ID", "") or "").strip()
        if channel_id:
            ok = post_to_clickup_channel(msg, channel_id)
            print("Posted to ClickUp:", ok)
        else:
            print("EOD_CLICKUP_CHANNEL_ID not set — skipping post")
        return
    if getattr(args, "trend_report", None) and str(args.trend_report).strip():
        end_iso = str(args.trend_report).strip()
        employee = (CLICKUP_USER_EMAIL or USER_EMAIL or "").strip() or "unknown"
        out = build_performance_trend(employee, end_iso, weeks=4, out_dir=OUTPUT_DIR)
        print(f"Trend report written: {OUTPUT_DIR / f'trend_{end_iso}.json'}")
        # also print top line insight for CLI use
        if isinstance(out, dict) and out.get("insight"):
            print(str(out.get("insight")))
        return
    if getattr(args, "skill_profile", None) and str(args.skill_profile).strip():
        end_iso = str(args.skill_profile).strip()
        path = write_skill_profile(end_iso, args.skill_window, employee=None)
        print(f"Skill profile written: {path}")
        return

    date_str = args.date

    start = f"{date_str}T00:00:00+05:30"
    end = f"{date_str}T23:59:59+05:30"

    web = []
    for b in WEB_BUCKETS:
        web.extend(pull(b, start, end))

    window = pull(WINDOW_BUCKET, start, end)
    afk = pull(AFK_BUCKET, start, end)

    web_summary = summarise_web_events(web)
    top_dom = web_summary["domains_top"][:5]
    dom_line = ", ".join(f"{x['domain']} ({x['minutes']:.0f}m)" for x in top_dom) if top_dom else "—"
    print(f"Web events: {len(web)} | Window: {len(window)} | AFK: {len(afk)}")
    print(f"Web domains (top 5 by time): {dom_line}")

    input_events, afk_events = fetch_input_and_afk_events(date_str)
    daily_input = aggregate_daily_input_from_events(input_events, afk_events)
    daily_input["date"] = date_str
    daily_input["input_source"] = "input_watcher + afk_watcher"
    print(
        f"Input summary: active {daily_input['active_minutes']:.1f} min, "
        f"idle {daily_input['idle_minutes']:.1f} min, "
        f"activity {daily_input['activity_rate']:.1f}%, "
        f"keys {daily_input['keystrokes']}, clicks {daily_input['mouse_clicks']}"
    )

    sessions = build_sessions(web, window, afk)
    print(f"Session summaries: {len(sessions)}")

    # Google Calendar (primary) → time overlap marks meeting + event title; requires Credentials.json + OAuth token.
    calendar_events: list = []
    if process_sessions_with_calendar is not None:
        sessions, calendar_events = process_sessions_with_calendar(
            sessions,
            date_str,
            verbose_list=args.debug_clickup,
            log_matches=True,
        )
    else:
        print("  [calendar] skipped (install: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client)")

    # Split sessions at meeting boundaries so standup doesn't merge with post-standup work.
    if calendar_events:
        sessions = split_sessions_at_meeting_boundaries(sessions, calendar_events, window_events=window)

    # Title/heuristics + optional ClickUp Calls list → refine or add meetings not matched by calendar.
    sessions = process_meeting_sessions(sessions, CLICKUP_CALLS_LIST_ID, debug=args.debug_clickup)

    raw_tasks: list = []
    clickup_tasks: list = []
    clickup_time_entries = []
    try:
        cu = ClickUpClient()
        if cu.token:
            teams = cu.get_teams()
            team_id = CLICKUP_TEAM_ID or (teams[0]["id"] if teams else "")
            members = cu.get_team_members()
            user = next(
                (u for u in members.values() if (u.get("email") or "").lower() == (CLICKUP_USER_EMAIL or "").lower()),
                None,
            )
            if args.debug_clickup:
                print(f"[clickup] team_id={team_id} user_email={CLICKUP_USER_EMAIL} user_id={user['id'] if user else None}")
            if team_id and user:
                raw_tasks = cu.get_tasks_for_user(team_id, user["id"], statuses=["in progress", "open"])
                day_start_ms = int(
                    _dt.datetime.strptime(f"{date_str} 00:00:00", "%Y-%m-%d %H:%M:%S").timestamp() * 1000
                )
                day_end_ms = day_start_ms + 86_400_000
                try:
                    touched_today = cu.get_team_tasks_updated_between(
                        team_id, user["id"], day_start_ms, day_end_ms
                    )
                except Exception:
                    touched_today = []
                by_tid: dict[str, dict] = {}
                for t in raw_tasks or []:
                    tid = t.get("id")
                    if tid is not None:
                        by_tid[str(tid)] = t
                for t in touched_today or []:
                    tid = t.get("id")
                    if tid is None:
                        continue
                    ts = str(tid)
                    if ts not in by_tid:
                        by_tid[ts] = t
                merged_roots = list(by_tid.values())
                clickup_tasks = cu.expand_assignee_tasks_with_nested_subtasks(merged_roots)
                start_ms = day_start_ms
                end_ms = day_end_ms - 1
                try:
                    clickup_time_entries = cu.get_time_entries(team_id, user["id"], start_ms, end_ms)
                except Exception:
                    clickup_time_entries = []
                if args.debug_clickup:
                    print(
                        f"[clickup] tasks_open_or_progress={len(raw_tasks)} "
                        f"touched_today_extra={len(touched_today)} "
                        f"merged_roots={len(merged_roots)} expanded={len(clickup_tasks)} "
                        f"time_entries={len(clickup_time_entries)}"
                    )
                    for t in clickup_tasks:
                        name = (t.get("name") or "").strip() or "(no name)"
                        tid = t.get("id")
                        par = t.get("parent")
                        has_parent = bool(
                            (isinstance(par, str) and par.strip())
                            or (isinstance(par, dict) and par.get("id"))
                        )
                        prefix = "  └" if has_parent else "   "
                        print(f"{prefix} {name} [{tid}]")
    except Exception:
        clickup_tasks = []
        clickup_time_entries = []

    meetings_today: list[str] = []
    for s in sessions:
        if str(s.get("zone") or "").lower() == "meeting":
            mm = s.get("meeting_match") or {}
            n = (mm.get("task_name") or "").strip()
            if n and n not in meetings_today:
                meetings_today.append(n)

    daily_mapping_context = {
        "employee_name": (CLICKUP_USER_EMAIL or USER_EMAIL or "").strip() or "the developer",
        "meetings_today": meetings_today,
    }
    results = map_sessions_to_tasks(
        sessions,
        clickup_tasks,
        clickup_time_entries,
        daily_context=daily_mapping_context,
    )

    for r in results:
        r["app_breakdown"] = build_app_breakdown(r, window, web)
        r["input"] = get_session_input_summary(r, input_events, afk_events)

    # Clean up tiny unknown gaps and auto-name unmapped slots so the UI never shows “empty” work.
    results = _coalesce_micro_sessions(results)
    for r in results:
        name = (r.get("clickup_task_name") or "").strip()
        if name:
            continue
        zone = str(r.get("zone") or "").lower().strip()
        # Only auto-name sessions that aren't confidently mapped to a ClickUp task.
        if zone in ("unknown", "unclear", "meeting", "untracked_work", "untracked", ""):
            r["clickup_task_name"] = f"AI says: {_guess_label_from_context(r)}"

    for r in results:
        print("\n--- SESSION ---")
        print(f"{r['start']} → {r['end']} ({(r['duration_min']/60.0):.2f} h)")
        print("Zone:", r.get("zone"))
        print("Map:", r.get("map_method"), "conf", r.get("map_confidence"), "tier", r.get("map_tier"))
        print("ClickUp task:", r.get("clickup_task_id"), r.get("clickup_task_name"))
        si = r.get("input") or {}
        print(
            f"Input: {si.get('keystrokes', 0)} keystrokes · {si.get('mouse_clicks', 0)} clicks · "
            f"{si.get('activity_rate', 0):.0f}% active"
        )
        if r.get("map_notes"):
            print("Notes:", r.get("map_notes")[:120])
        print("Apps:", r["apps"])
        print("URLs:", r["urls"])
        print("Titles:", r.get("titles"))

    zone_minutes = defaultdict(float)
    for r in results:
        z = r.get("zone") or "unclear"
        zone_minutes[z] += float(r.get("duration_min", 0.0) or 0.0)

    total_z = sum(zone_minutes.values())
    print("\n=== ZONE BREAKDOWN (hours) ===")
    for k, v in sorted(zone_minutes.items()):
        pct = (v / total_z) * 100 if total_z else 0.0
        print(f"{k}: {(v/60.0):.2f} h ({pct:.1f}%)")

    if not args.write_out:
        return

    write_sessions(date_str, results)

    # Activity segments (timeline-only; sessions remain for EOD/productivity)
    out_dir = OUTPUT_DIR
    task_by_id: dict[str, dict] = {}
    for t in clickup_tasks or []:
        tid = t.get("id")
        if tid is not None:
            task_by_id[str(tid)] = t

    segments = build_segments_with_web(window, afk, web, date_str)
    print(f"Segments built: {len(segments)}")
    mapped_segments = map_segments(
        segments,
        clickup_tasks,
        task_by_id,
        calendar_events,
        (CLICKUP_USER_EMAIL or USER_EMAIL or "").strip() or "the developer",
        date_str,
    )
    write_segments(mapped_segments, date_str, out_dir)

    active_minutes = float(sum(r.get("duration_min", 0.0) or 0.0 for r in results))

    url_counter = Counter()
    peak_tab_count = 0
    for r in results:
        urls = [u for u in (r.get("urls") or []) if not str(u).startswith("APP_CONTEXT:")]
        url_counter.update(urls)
        peak_tab_count = max(peak_tab_count, len(urls))

    top_urls = [u for u, _c in url_counter.most_common(10)]

    totals = {
        "active_minutes": active_minutes,
        "idle_minutes": daily_input["idle_minutes"],
        "active_input_minutes": daily_input["active_minutes"],
        "productivity_pct": daily_input["activity_rate"],
        "session_count": len(results),
        "task_linked_minutes": float(zone_minutes.get("task_linked", 0.0)),
        "meeting_minutes": float(zone_minutes.get("meeting", 0.0)),
        "untracked_minutes": float(zone_minutes.get("untracked_work", 0.0)),
        "unclear_minutes": float(zone_minutes.get("unclear", 0.0)),
        "unknown_minutes": float(zone_minutes.get("unknown", 0.0)),
        "top_urls": top_urls,
        "peak_tab_count": int(peak_tab_count),
        "web_domain_summary": web_summary,
    }
    write_daily_summary(date_str, dict(zone_minutes), totals)

    eod = generate_eod_summary(date_str, USER_EMAIL)
    push_daily_summary(date_str, OUTPUT_DIR)
    push_eod_report(date_str, OUTPUT_DIR)
    push_skill_profile(date_str, OUTPUT_DIR)

    msg = format_eod_clickup_message(eod)

    print("\n" + "=" * 50)
    print("EOD SUMMARY")
    print("=" * 50)
    print(msg)

    channel_id = (os.getenv("EOD_CLICKUP_CHANNEL_ID", "") or "").strip()
    if channel_id:
        ok = post_to_clickup_channel(msg, channel_id)
        print("Posted EOD to ClickUp:", ok)
    else:
        print("EOD_CLICKUP_CHANNEL_ID not set — skipping ClickUp post")


if __name__ == "__main__":
    main()
