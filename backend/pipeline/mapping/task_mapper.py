"""
Task mapping pipeline: 3 certain-signal checks → AI primary classifier.

Priority order:
  1. ClickUp time-entry overlap  (ground truth from timer)
  2. Google Calendar exact match (set by gcal/client.py before this runs)
  3. ClickUp task URL in session  (validated against activity)
  4. AI classification (Gemini Flash) — primary path for everything else
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from utils.helpers import get_domain

_vertex_client = None
_genai_types = None


def _get_vertex_client():
    global _vertex_client, _genai_types
    print("[task_mapper] _get_vertex_client called")
    print(f"[task_mapper] cached={_vertex_client is not None}")
    if _vertex_client is not None:
        return _vertex_client
    project = os.getenv("GCP_PROJECT_ID", "").strip()
    region = os.getenv("GCP_REGION", "us-central1").strip()
    print(
        f"[task_mapper] project={project!r} "
        f"key_set={bool((os.getenv('GEMINI_API_KEY') or '').strip())}"
    )
    if not project:
        return None
    try:
        from google import genai
        from google.genai import types as genai_types_module
    except ImportError:
        return None

    # Try Vertex AI first
    try:
        cred_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        if cred_file and os.path.exists(cred_file):
            from google.oauth2 import service_account

            credentials = service_account.Credentials.from_service_account_file(
                cred_file,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            client = genai.Client(
                vertexai=True,
                project=project,
                location=region,
                credentials=credentials,
            )
        else:
            client = genai.Client(
                vertexai=True,
                project=project,
                location=region,
            )
        _vertex_client = client
        _genai_types = genai_types_module
        return _vertex_client
    except Exception as e:
        print(f"[task_mapper] Vertex FAILED: {type(e).__name__}: {e}")

    # Fallback to Gemini API key
    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if api_key:
        try:
            client = genai.Client(api_key=api_key)
            _vertex_client = client
            _genai_types = genai_types_module
            print(f"[task_mapper] API key fallback key_set={bool(api_key)}")
            print("[task_mapper] Using Gemini API key fallback")
            return _vertex_client
        except Exception as e:
            print(f"[task_mapper] Gemini API key fallback failed: {e}")
    print(f"[task_mapper] API key fallback key_set={bool(api_key)}")

    return None


# ── Keyword utilities (used by URL validation) ────────────────────────────────

_ALWAYS_SKIP = frozenset(
    {
        "py", "ts", "tsx", "js", "jsx",
        "css", "html", "json", "md",
        "com", "org", "io", "www", "http", "https",
    }
)


def extract_keywords(text: str) -> list[str]:
    if not text:
        return []
    text = _strip_app_suffix(text)
    parts = re.split(r"[/\\.\-_:\s]+", text.lower())
    out = []
    for p in parts:
        p = p.strip()
        if len(p) > 2 and p not in _ALWAYS_SKIP:
            out.append(p)
    return out


def _strip_app_suffix(text: str) -> str:
    return re.sub(
        r"[—\-]\s*(Visual Studio Code|Cursor|VS Code|Code).*$",
        "",
        text,
        flags=re.I,
    ).strip()


def _strip_browser_suffix(text: str) -> str:
    t = text
    for suf in (
        " - Brave",
        " - Google Chrome",
        " - Chromium",
        " - Firefox",
        " — Brave",
        " — Google Chrome",
    ):
        if t.endswith(suf):
            t = t[: -len(suf)]
    return t.strip()


# ── URL-based detection ───────────────────────────────────────────────────────

_NEUTRAL_HOSTS = frozenset({
    "gmail.com", "mail.google.com", "calendar.google.com",
    "accounts.google.com", "zoom.us", "meet.google.com",
    "tracker.toptal.com", "app.clickup.com", "clickup.com",
    "localhost", "127.0.0.1",
})

IST = timezone(timedelta(hours=5, minutes=30))


def _clickup_task_ids_from_urls(session: dict) -> list[str]:
    """Task IDs from ClickUp task URLs (…/t/{id}/…)."""
    out: list[str] = []
    seen: set[str] = set()
    for u in session.get("urls") or []:
        us = str(u)
        if "clickup.com" not in us.lower():
            continue
        for m in re.finditer(r"/t/([a-z0-9]+)", us, flags=re.I):
            tid = m.group(1).lower()
            if tid not in seen:
                seen.add(tid)
                out.append(tid)
    return out


def _match_task_by_clickup_url(session: dict, task_by_id: dict[str, dict]) -> dict | None:
    for tid in _clickup_task_ids_from_urls(session):
        row = task_by_id.get(tid)
        if row:
            return row
    return None


def _clickup_url_confidence(
    session: dict,
    task: dict,
    task_by_id: dict[str, dict],
) -> tuple[float, str]:
    """
    Validate a ClickUp URL match against actual session activity.

    0.98 — keyword overlap between task name and session signals (confirmed)
    0.80 — no contradiction, neutral signals (uncertain but probable)
    0.55 — project-specific signals clearly point to a different task (contradicted)
    """
    task_name = _display_task_name(task, task_by_id)
    task_kws = set(extract_keywords(task_name))
    if not task_kws:
        return 0.80, "URL matched, no task keywords to validate"

    meaningful: list[str] = []
    for raw in (session.get("titles") or []):
        t = _strip_browser_suffix(_strip_app_suffix(str(raw))).strip()
        tl = t.lower()
        if not t or tl in {"loading", "untitled", "new tab", "meeting", "unknown"}:
            continue
        meaningful.append(t)

    for url_str in (session.get("urls") or []):
        us = str(url_str)
        if us.startswith("APP_CONTEXT:"):
            continue
        try:
            p = urlparse(us)
            host = (p.netloc or "").replace("www.", "").lower()
            if host in _NEUTRAL_HOSTS or host.endswith(".zoom.us"):
                continue
            if any(svc in host for svc in ("figma.com", "docs.google.com", "sheets.google.com", "drive.google.com")):
                for seg in (p.path or "").split("/"):
                    cleaned = seg.replace("-", " ").replace("_", " ").strip()
                    if len(cleaned) > 4 and not cleaned.isdigit():
                        meaningful.append(cleaned)
        except Exception:
            pass

    if not meaningful:
        return 0.80, "URL matched, no meaningful activity signals to validate"

    all_signal_kws: set[str] = set()
    project_signal_count = 0

    for sig in meaningful:
        kws = set(extract_keywords(sig))
        if not kws:
            continue
        all_signal_kws |= kws
        if len(kws) >= 2:
            project_signal_count += 1

    overlap = task_kws & all_signal_kws
    if overlap:
        hit = ", ".join(sorted(overlap)[:3])
        return 0.98, f"validated: '{hit}' in activity signals"

    if project_signal_count >= 2:
        return 0.55, "URL matched but activity signals don't align with task name"

    return 0.80, "URL matched, activity signals are neutral (no contradiction)"


# ── AI prompt helpers ─────────────────────────────────────────────────────────

def _task_description_snip(t: dict, max_len: int = 160) -> str:
    d = (t.get("description") or "").strip().replace("\n", " ")
    if len(d) > max_len:
        return d[: max_len - 1] + "…"
    return d


def _clickup_tasks_prompt_block(tasks: list[dict], task_by_id: dict[str, dict]) -> str:
    """Nested parent → subtasks for the model (IDs must match API)."""
    children: dict[str, list[dict]] = defaultdict(list)
    roots: list[dict] = []
    for t in tasks:
        tid = t.get("id")
        if tid is None:
            continue
        pid = _parent_id(t)
        if pid:
            children[str(pid)].append(t)
        else:
            roots.append(t)

    lines: list[str] = []

    def emit(t: dict, depth: int) -> None:
        if depth > 14:
            return
        tid = str(t.get("id") or "")
        if not tid:
            return
        indent = "  " * depth
        nm = (t.get("name") or "").strip()
        desc = _task_description_snip(t)
        extra = f" — {desc}" if desc else ""
        lines.append(f"{indent}- [{tid}] {nm}{extra}")
        for ch in sorted(children.get(tid, []), key=lambda x: (str(x.get("name") or ""), str(x.get("id") or ""))):
            emit(ch, depth + 1)

    for r in sorted(roots, key=lambda x: (str(x.get("name") or ""), str(x.get("id") or ""))):
        emit(r, 0)
    return "\n".join(lines[:220])


def _session_time_ist(session: dict) -> tuple[str, str, str, float | None]:
    st = _parse_dt(session.get("start"))
    et = _parse_dt(session.get("end"))
    if not st or not et:
        return "—", "—", "—", None
    if st.tzinfo is None:
        st = st.replace(tzinfo=IST)
    else:
        st = st.astimezone(IST)
    if et.tzinfo is None:
        et = et.replace(tzinfo=IST)
    else:
        et = et.astimezone(IST)
    dur = max(0.0, (et - st).total_seconds() / 60.0)
    return (
        st.strftime("%Y-%m-%d %H:%M IST"),
        et.strftime("%Y-%m-%d %H:%M IST"),
        st.strftime("%A"),
        dur,
    )


def _app_time_breakdown_from_session(session: dict) -> str:
    """
    Generic activity signals for the model.

    Prefer precomputed per-app breakdown if present; otherwise fall back to the raw
    list of apps observed during the session.
    """
    bd = session.get("app_breakdown")
    if isinstance(bd, list) and bd:
        lines: list[str] = []
        for row in bd[:20]:
            if not isinstance(row, dict):
                continue
            name = row.get("app") or row.get("name") or row.get("title") or ""
            name = str(name).strip()
            if not name:
                continue
            minutes = row.get("minutes")
            seconds = row.get("seconds")
            pct = row.get("pct") or row.get("percentage")
            extra = ""
            if minutes is not None:
                try:
                    extra = f" — {float(minutes):.1f} min"
                except Exception:
                    extra = f" — {minutes}"
            elif seconds is not None:
                try:
                    extra = f" — {float(seconds) / 60.0:.1f} min"
                except Exception:
                    extra = f" — {seconds}s"
            elif pct is not None:
                try:
                    extra = f" — {float(pct):.1f}%"
                except Exception:
                    extra = f" — {pct}%"
            lines.append(f"  - {name}{extra}")
        return "\n".join(lines) if lines else "  (none)"

    apps = [str(a).strip() for a in (session.get("apps") or []) if str(a).strip()]
    if not apps:
        return "  (none)"
    return "\n".join(f"  - {a}" for a in apps[:20])


def _tab_time_breakdown_from_session(session: dict) -> str:
    """
    More granular than per-app: show time spent per tab/title where possible.
    Uses the precomputed app_breakdown produced by integrations/aw/session_builder.py.
    """
    bd = session.get("app_breakdown")
    if not (isinstance(bd, list) and bd):
        return "  (no per-tab breakdown available)"

    lines: list[str] = []
    for row in bd[:20]:
        if not isinstance(row, dict):
            continue
        app = str(row.get("app") or "").strip()
        tabs = row.get("tabs") if isinstance(row.get("tabs"), list) else []
        if not app or not tabs:
            continue
        for t in tabs[:12]:
            if not isinstance(t, dict):
                continue
            title = str(t.get("title") or "").strip()
            minutes = t.get("minutes")
            if not title:
                continue
            try:
                m = float(minutes) if minutes is not None else None
            except Exception:
                m = None
            extra = f": {m:.1f}m" if m is not None else ""
            lines.append(f"  - {app} — {title}{extra}")

    return "\n".join(lines) if lines else "  (no per-tab breakdown available)"


def _browser_tabs_from_session(session: dict) -> str:
    """
    Prefer per-tab time from app_breakdown (browser + other apps with tab lists).
    Falls back to flat TITLES/URLS when breakdown is unavailable.
    """

    def _label_app(app: str) -> str:
        a = (app or "").strip()
        al = a.lower()
        if "chrome" in al:
            return "Chrome"
        if "brave" in al:
            return "Brave"
        if "firefox" in al:
            return "Firefox"
        if "clickup" in al:
            return "ClickUp"
        if "cursor" in al or "vscode" in al or a == "Code":
            return "Cursor"
        return a or "App"

    bd = session.get("app_breakdown")
    if isinstance(bd, list) and bd:
        rows: list[tuple[float, str]] = []
        for app_row in bd:
            if not isinstance(app_row, dict):
                continue
            app = _label_app(str(app_row.get("app") or ""))
            tabs = app_row.get("tabs") if isinstance(app_row.get("tabs"), list) else []
            for t in tabs:
                if not isinstance(t, dict):
                    continue
                title = str(t.get("title") or "").strip()
                if not title:
                    continue
                try:
                    m = float(t.get("minutes") or 0.0)
                except Exception:
                    m = 0.0
                if m <= 0:
                    continue
                rows.append((m, f'  {app} - "{title}" - {m:.0f}m'))

        rows.sort(key=lambda x: x[0], reverse=True)
        if rows:
            body = "\n".join(line for _m, line in rows[:40])
            return f"BROWSER TAB BREAKDOWN (time per tab):\n{body}"

    titles = [str(t) for t in (session.get("titles") or []) if t]
    urls = [str(u) for u in (session.get("urls") or []) if u and not str(u).startswith("APP_CONTEXT:")]
    title_block = "\n".join(f"  - {t}" for t in titles[:18]) if titles else "  (none)"
    url_block = "\n".join(f"  - {u}" for u in urls[:12]) if urls else "  (none)"
    return f"TITLES:\n{title_block}\n\nURLS:\n{url_block}"


def _generate_classify_content(client, prompt: str):
    """Single generate_content call for session classification (Vertex or Gemini API client)."""
    if _genai_types is None:
        raise RuntimeError("genai types not loaded")
    return client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=_genai_types.GenerateContentConfig(temperature=0),
    )


# ── AI primary classifier ─────────────────────────────────────────────────────

def _ai_classify_session(
    session: dict,
    tasks: list[dict],
    task_by_id: dict[str, dict],
    *,
    calendar_events: list[dict],
    employee_name: str,
    previous_task_summaries: list[str],
) -> dict:
    """
    Primary classifier: full session context → zone + task/meeting assignment.

    Returns a field dict ready to be merged into the session output.
    Falls back to zone=unknown when Gemini is unavailable or errors.
    """
    _UNAVAILABLE: dict = {
        "zone": "unknown",
        "clickup_task_id": None,
        "clickup_task_name": None,
        "map_confidence": 0.0,
        "map_method": "none",
        "map_tier": None,
        "map_notes": "AI unavailable (Vertex AI not configured — set GCP_PROJECT_ID, GCP_REGION, GOOGLE_APPLICATION_CREDENTIALS; for DNS/connectivity failures set GEMINI_API_KEY or GOOGLE_API_KEY for Gemini API fallback)",
    }

    client = _get_vertex_client()
    if client is None or _genai_types is None:
        return _UNAVAILABLE

    t_start, t_end, dow, dur_min = _session_time_ist(session)
    dur_str = f"{dur_min:.0f}" if dur_min is not None else "?"

    # Calendar events in IST for the prompt
    if calendar_events:
        cal_lines: list[str] = []
        for ev in calendar_events:
            s_ts = ev.get("start_ts")
            e_ts = ev.get("end_ts")
            name = ev.get("name", "Unnamed event")
            if s_ts and e_ts:
                s_str = datetime.fromtimestamp(s_ts, tz=IST).strftime("%H:%M")
                e_str = datetime.fromtimestamp(e_ts, tz=IST).strftime("%H:%M")
                cal_lines.append(f"  - {name} ({s_str}–{e_str} IST)")
        cal_block = "\n".join(cal_lines) if cal_lines else "  (none today)"
    else:
        cal_block = "  (none today)"

    prev_block = (
        "\n".join(f"  - {p}" for p in previous_task_summaries[-8:])
        if previous_task_summaries
        else "  (first session today)"
    )

    app_time_breakdown = _app_time_breakdown_from_session(session)
    tab_time_breakdown = _tab_time_breakdown_from_session(session)
    browser_tabs = _browser_tabs_from_session(session)
    task_block = _clickup_tasks_prompt_block(tasks, task_by_id) if tasks else "  (no open tasks)"

    prompt = f"""You are classifying a work session for {employee_name} at Value at Void (a design agency).

SESSION:
  Time: {t_start} → {t_end} ({dur_str} min), {dow}

ACTIVITY SIGNALS:
{app_time_breakdown}

TAB / TITLE TIME BREAKDOWN (use this to weight dominant work):
{tab_time_breakdown}

BROWSER TABS:
{browser_tabs}

TODAY'S CALENDAR EVENTS:
{cal_block}

EARLIER SESSIONS TODAY (what came before this one):
{prev_block}

OPEN CLICKUP TASKS (parent → subtask hierarchy):
{task_block}

DECISION RULES:

SYNCUP CALL DETECTION:
If ClickUp app is open AND any title contains 'SyncUp Call with' → this is a MEETING
ZONE: meeting
MEETING_NAME: extract the name after 'SyncUp Call with'
e.g. 'SyncUp Call with Priyanshu' → meeting name = 'SyncUp Call with Priyanshu'
Treat exactly like a Zoom/calendar meeting.

1. If dominant activity (>50% time) is entertainment/social:
   youtube.com, netflix.com, instagram.com, twitter.com,
   reddit.com, music streaming, news sites
   → ZONE: untracked_work
   → TASK_ID: [none]
   → REASON: describe what they were actually doing

2. If Zoom/Meet active + calendar event overlaps:
   → ZONE: meeting
   → use the calendar event name

3. If work tools active (Figma, Cursor, VSCode, Sheets,
   Docs, Notion, ClickUp, GitHub, any design/dev tool):
   → ZONE: task_linked
   → Match to most specific ClickUp subtask possible
   → If certain match: CONFIDENCE 0.75-0.95
   → If best guess: CONFIDENCE 0.50-0.74,
      add (inferred) to REASON

IMPORTANT: Any tool-specific signal is valid —
   Figma projects, Google Docs, GitHub repos,
   design files, spreadsheets, email subjects.
   Match the activity to the most specific
   ClickUp subtask possible regardless of role.

4. If ClickUp task URL open but contradicting activity:
   → Ignore the URL
   → Use actual activity signals to determine task

5. NEVER return unknown
   Always make the most reasonable inference
   Low confidence is better than no answer

6. For each task match, show your reasoning:
   What specific signals led to this conclusion?
   Which apps/tabs/files were strongest evidence?

Response format:
ZONE: meeting|task_linked|untracked_work
TASK_ID: [id] or [none]
MEETING_NAME: [name] or [none]
CONFIDENCE: 0.50-1.0
SIGNALS: comma separated list of what drove the decision
REASON: one clear sentence"""

    try:
        resp = _generate_classify_content(client, prompt)
        text = (resp.text or "").strip()

        # Parse the structured response
        parsed: dict[str, str] = {}
        for line in text.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                parsed[k.strip().upper()] = v.strip()

        zone = parsed.get("ZONE", "task_linked").lower().strip()
        if zone not in ("meeting", "task_linked", "untracked_work"):
            zone = "task_linked"

        raw_tid = parsed.get("TASK_ID", "[none]").strip()
        raw_meeting = parsed.get("MEETING_NAME", "[none]").strip()
        raw_conf = parsed.get("CONFIDENCE", "0.65").strip()
        signals = parsed.get("SIGNALS", "").strip()
        reason = parsed.get("REASON", "AI classification")[:400]

        try:
            confidence = max(0.50, min(1.0, float(raw_conf)))
        except (ValueError, TypeError):
            confidence = 0.65

        if zone == "meeting":
            mname = raw_meeting.strip().strip("[]").strip()
            if mname.lower() in ("none", ""):
                mname = "Meeting"
            return {
                "zone": "meeting",
                "clickup_task_id": None,
                "clickup_task_name": mname,
                "map_confidence": confidence,
                "map_method": "ai_classify",
                "map_tier": 1 if confidence >= 0.85 else 2,
                "map_notes": (signals + " — " if signals else "") + reason,
                "meeting_match": {
                    "matched": True,
                    "task_id": None,
                    "task_name": mname,
                    "confidence": confidence,
                    "match_source": "ai_classify",
                    "note": (signals + " — " if signals else "") + reason,
                },
            }

        if zone == "untracked_work":
            return {
                "zone": "untracked_work",
                "clickup_task_id": None,
                "clickup_task_name": None,
                "map_confidence": confidence,
                "map_method": "ai_classify",
                "map_tier": 2,
                "map_notes": (signals + " — " if signals else "") + reason,
            }

        # task_linked: find the task by id
        tid = None
        m = re.search(r"\[([a-z0-9]+)\]", raw_tid, flags=re.I)
        if m:
            tid = m.group(1).lower()
        else:
            rt = raw_tid.strip().strip("[]").strip().lower()
            if rt and rt not in ("none",):
                tid = rt

        matched = None
        if tid:
            matched = task_by_id.get(tid) or next((t for t in tasks if str(t.get("id") or "").lower() == tid), None)
        if matched:
            return {
                "zone": "task_linked",
                "clickup_task_id": str(matched.get("id")),
                "clickup_task_name": _display_task_name(matched, task_by_id),
                "map_confidence": confidence,
                "map_method": "ai_classify",
                "map_tier": 1 if confidence >= 0.85 else 2,
                "map_notes": (signals + " — " if signals else "") + reason,
            }

        # Still must not return unknown: keep task_linked with low confidence.
        return {
            "zone": "task_linked",
            "clickup_task_id": None,
            "clickup_task_name": None,
            "map_confidence": max(0.50, min(0.74, confidence)),
            "map_method": "ai_classify",
            "map_tier": 2,
            "map_notes": (signals + " — " if signals else "") + (reason + " (inferred)"),
        }

    except Exception as exc:
        return {
            "zone": "unknown",
            "clickup_task_id": None,
            "clickup_task_name": None,
            "map_confidence": 0.0,
            "map_method": "none",
            "map_tier": None,
            "map_notes": f"AI error: {str(exc)[:120]}",
        }


# ── Task display / lookup helpers ─────────────────────────────────────────────

def _parent_id(task: dict) -> str | None:
    p = task.get("parent")
    if p is None:
        return None
    if isinstance(p, dict):
        tid = p.get("id")
        return str(tid) if tid is not None else None
    if isinstance(p, str) and p.strip():
        return str(p.strip())
    return None


def _is_subtask_task(task: dict) -> bool:
    return _parent_id(task) is not None


def _display_task_name(task: dict, task_by_id: dict[str, dict]) -> str:
    """Show 'Parent — Subtask' when we matched a subtask (Trace UI clarity)."""
    name = (task.get("name") or "").strip()
    if not name:
        return "—"
    pid = _parent_id(task)
    if not pid:
        return name
    parent = task_by_id.get(pid)
    if not parent:
        return name
    pn = (parent.get("name") or "").strip()
    if not pn:
        return name
    nl, pl = name.lower(), pn.lower()
    if nl in pl or pl in nl:
        return name
    return f"{pn} — {name}"


def _is_work_signal(session: dict) -> bool:
    """Best-effort: does the session look like active work vs idle browsing?"""
    apps = [str(a).lower() for a in (session.get("apps") or [])]
    urls = [str(u).lower() for u in (session.get("urls") or []) if not str(u).startswith("APP_CONTEXT:")]

    work_apps = ("cursor", "code", "terminal", "figma", "slack", "notion", "clickup")
    if any(any(w in a for w in work_apps) for a in apps):
        return True

    work_domains = (
        "github.com", "gitlab.com", "figma.com", "notion.so",
        "docs.google.com", "claude.ai", "chatgpt.com",
        "vercel.com", "clickup.com", "app.clickup.com",
    )
    for u in urls:
        d = get_domain(u) if u.startswith("http") else ""
        if any(w in d for w in work_domains):
            return True
        if "clickup" in u:
            return True

    return False


# ── Time / overlap helpers ────────────────────────────────────────────────────

def _parse_dt(v):
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v)
        except Exception:
            return None
    return None


def _ms_to_dt(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000)
    except Exception:
        return None


def _overlap_minutes(a_start, a_end, b_start, b_end):
    if not (a_start and a_end and b_start and b_end):
        return 0.0
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    if end <= start:
        return 0.0
    return (end - start).total_seconds() / 60.0


# ── Main pipeline ─────────────────────────────────────────────────────────────

def map_sessions_to_tasks(
    sessions: list[dict],
    tasks: list[dict] | None = None,
    time_entries: list[dict] | None = None,
    calendar_events: list[dict] | None = None,
    min_keyword_score: int = 3,  # kept for backward compatibility, not used
    daily_context: dict | None = None,
) -> list[dict]:
    """
    Enrich each session with zone, ClickUp fields, and map_* metadata.

    Priority:
      1. ClickUp time-entry overlap  → ground truth, highest certainty
      2. Google Calendar exact match → set by gcal/client.py, certain
      3. ClickUp task URL in session → near-certain with activity validation
      4. AI classification (Gemini)  → primary path for everything else
    """
    print(
        f"[task_mapper] mapping {len(sessions)} sessions "
        f"against {len(tasks or [])} tasks"
    )
    tasks = tasks or []
    time_entries = time_entries or []
    calendar_events = calendar_events or []
    daily_context = daily_context or {}
    employee_name = str(daily_context.get("employee_name") or "the developer").strip() or "the developer"
    prev_task_labels: list[str] = []

    def _track_previous(out_sess: dict) -> None:
        nm = (out_sess.get("clickup_task_name") or "").strip()
        if nm:
            prev_task_labels.append(nm)
            while len(prev_task_labels) > 10:
                prev_task_labels.pop(0)

    task_by_id: dict[str, dict] = {}
    for t in tasks:
        tid = t.get("id")
        if tid is not None:
            task_by_id[str(tid)] = t

    aligned: list[dict] = []

    for s in sessions:
        out = dict(s)
        out.setdefault("summary", "")

        # ── 1. ClickUp time-entry overlap (ground truth) ─────────────────────
        if time_entries:
            s_start = _parse_dt(out.get("start"))
            s_end = _parse_dt(out.get("end"))
            best_te = None
            best_overlap = 0.0
            for te in time_entries:
                te_start = _ms_to_dt(te.get("start"))
                te_end = _ms_to_dt(te.get("end")) or _ms_to_dt(
                    (te.get("start") or 0) + (te.get("duration") or 0)
                )
                ov = _overlap_minutes(s_start, s_end, te_start, te_end)
                if ov > best_overlap:
                    best_overlap = ov
                    best_te = te

            if best_te and best_overlap >= 1.0:
                task = best_te.get("task") or {}
                tid = task.get("id") or best_te.get("task_id")
                tid_s = str(tid) if tid is not None else None
                row = task_by_id.get(tid_s) if tid_s else None
                disp = _display_task_name(row, task_by_id) if row else (task.get("name") or None)
                out["zone"] = "task_linked"
                out["clickup_task_id"] = tid_s
                out["clickup_task_name"] = disp
                out["map_confidence"] = 0.95
                out["map_method"] = "time_entry"
                out["map_tier"] = 1
                out["map_notes"] = f"overlap {best_overlap:.1f} min with logged time"
                _track_previous(out)
                aligned.append(out)
                continue

        # ── 2. Google Calendar exact match (already set upstream) ─────────────
        if out.get("zone") == "meeting" and (out.get("meeting_match") or {}).get("match_source") == "google_calendar":
            mm = out.get("meeting_match") or {}
            out["map_confidence"] = float(mm.get("confidence") or 0.95)
            out["map_method"] = "google_calendar"
            out["map_tier"] = 1
            out["map_notes"] = str(mm.get("task_name") or "")
            _track_previous(out)
            aligned.append(out)
            continue

        # ── 3. ClickUp task URL ───────────────────────────────────────────────
        url_hit = _match_task_by_clickup_url(out, task_by_id) if tasks else None
        if url_hit is not None:
            tid_s = str(url_hit.get("id"))
            conf, val_note = _clickup_url_confidence(out, url_hit, task_by_id)
            out["zone"] = "task_linked"
            out["clickup_task_id"] = tid_s
            out["clickup_task_name"] = _display_task_name(url_hit, task_by_id)
            out["map_confidence"] = conf
            out["map_method"] = "clickup_url"
            out["map_tier"] = 1
            out["map_notes"] = val_note
            _track_previous(out)
            aligned.append(out)
            continue

        # ── 4. AI primary classifier ──────────────────────────────────────────
        ai = _ai_classify_session(
            out,
            tasks,
            task_by_id,
            calendar_events=calendar_events,
            employee_name=employee_name,
            previous_task_summaries=list(prev_task_labels),
        )
        out.update(ai)

        # If AI returned unknown and there are truly no tasks, use work-signal heuristic
        # to distinguish "untracked work" (productive but unmapped) from "unclear" (idle).
        if out["zone"] == "unknown" and not tasks:
            out["zone"] = "untracked_work" if _is_work_signal(out) else "unclear"
            out["map_confidence"] = 0.0
            out["map_method"] = "none"

        _track_previous(out)
        aligned.append(out)

    # Post-process: enforce meeting duration cap
    for s in aligned:
        zone = s.get("zone", "")
        method = (s.get("map_method") or "").lower()
        duration = float(s.get("duration_min") or 0)
        notes = s.get("map_notes") or ""

        # If meeting was set by task mapper (not calendar)
        # and duration exceeds 60 minutes, revert to unclear
        if (
            zone == "meeting"
            and "calendar" not in method
            and "google" not in method
            and duration > 60
        ):
            s["zone"] = "unclear"
            s["map_method"] = "none"
            s["map_notes"] = (notes + " | meeting_reverted: duration exceeds 60min cap").strip(" |")
            s["clickup_task_id"] = None
            s["clickup_task_name"] = None
            if "meeting_match" in s:
                s["meeting_match"] = None

    return aligned
