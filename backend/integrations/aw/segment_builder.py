from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import BACKEND_ROOT

# Generic UI / window-manager noise only; org-specific titles live in
# backend/data/segment_noise_titles.json or SEGMENT_NOISE_TITLES_JSON.
_DEFAULT_NOISE_TITLES: frozenset[str] = frozenset(
    {
        "desktop icons 1",
        "zoom_linux_float_video_window",
        "zoom workplace",
        "zoom workplace - free account",
        "menu window",
        "sub menu window",
        "unknown",
        "current activity description",
        "select files this site can read",
        "loading",
        "new tab",
        "untitled",
    }
)

_noise_titles_cache: frozenset[str] | None = None


def _noise_title_paths() -> list[Path]:
    out: list[Path] = []
    env = (os.getenv("SEGMENT_NOISE_TITLES_JSON") or "").strip()
    if env:
        out.append(Path(env).expanduser())
    out.append(BACKEND_ROOT / "data" / "segment_noise_titles.json")
    return out


def noise_titles() -> frozenset[str]:
    """Built-in noise titles plus optional JSON list (no hardcoded org strings in code)."""
    global _noise_titles_cache
    if _noise_titles_cache is not None:
        return _noise_titles_cache
    extra: set[str] = set()
    for path in _noise_title_paths():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        raw_list: list[Any]
        if isinstance(data, list):
            raw_list = data
        elif isinstance(data, dict) and isinstance(data.get("titles"), list):
            raw_list = data["titles"]
        else:
            continue
        for t in raw_list:
            s = str(t).strip().lower()
            if s:
                extra.add(s)
    _noise_titles_cache = _DEFAULT_NOISE_TITLES | frozenset(extra)
    return _noise_titles_cache


def _parse_event_dt(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso_dt(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _strip_browser_suffix(title: str) -> str:
    t = (title or "").strip()
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


def _strip_editor_suffix(title: str) -> str:
    return re.sub(
        r"[—\-]\s*(Visual Studio Code|Cursor|VS Code|Code).*$",
        "",
        (title or "").strip(),
        flags=re.I,
    ).strip()


def _clean_title(app: str, title: str) -> str:
    al = (app or "").lower()
    t = (title or "").strip()
    if any(x in al for x in ("brave", "google-chrome", "chromium", "firefox", "chrome")):
        t = _strip_browser_suffix(t)
    if any(x in al for x in ("cursor", "code", "vscode")):
        t = _strip_editor_suffix(t)
    return t


def _stable_segment_id(start_iso: str, end_iso: str, app: str, title: str) -> str:
    key = f"{start_iso}|{end_iso}|{app}|{title}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def _build_title_url_map(web_events: list[dict]) -> dict[str, str]:
    m: dict[str, str] = {}
    for e in web_events or []:
        if not isinstance(e, dict):
            continue
        data = e.get("data") or {}
        title = str(data.get("title") or "").strip()
        url = str(data.get("url") or "").strip()
        if title and url and "APP_CONTEXT" not in url:
            m[title] = url
    return m


def _idle_cut_timestamps(afk_events: list[dict]) -> list[float]:
    """
    Timestamps where active work should be cut.

    - Start of any AFK interval with duration >= 5 minutes.
    - Start of any gap > 5 minutes between consecutive not-afk intervals.
    """
    idle_gap_s = 5 * 60
    cuts: list[float] = []

    parsed: list[tuple[float, float, str]] = []
    for e in afk_events or []:
        if not isinstance(e, dict):
            continue
        data = e.get("data") or {}
        status = str(data.get("status") or "").lower()
        if status not in ("afk", "not-afk"):
            continue
        t0 = _parse_event_dt(str(e.get("timestamp") or ""))
        if t0 is None:
            continue
        dur = float(e.get("duration") or 0.0)
        t1 = t0 + timedelta(seconds=dur)
        parsed.append((t0.timestamp(), t1.timestamp(), status))

    parsed.sort(key=lambda x: x[0])

    last_not_end: float | None = None
    for t0, t1, st in parsed:
        if st == "afk" and (t1 - t0) >= idle_gap_s:
            cuts.append(t0)
        if st == "not-afk":
            if last_not_end is not None and (t0 - last_not_end) > idle_gap_s:
                cuts.append(last_not_end)
            last_not_end = t1

    cuts.sort()
    return cuts


def _has_cut_between(cuts: list[float], a: float, b: float) -> bool:
    """True if any cut timestamp lies strictly between a and b."""
    for c in cuts:
        if a < c < b:
            return True
    return False


def build_segments(window_events: list[dict], afk_events: list[dict], date_str: str) -> list[dict]:
    """
    Convert raw ActivityWatch window events into activity segments.

    `date_str` is reserved for future day-bucketing; segment bounds come from AW timestamps.
    """
    _ = date_str
    return build_segments_with_web(window_events, afk_events, [], date_str)


def build_segments_with_web(window_events: list[dict], afk_events: list[dict], web_events: list[dict], date_str: str) -> list[dict]:
    _ = date_str
    title_url = _build_title_url_map(web_events)
    cuts = _idle_cut_timestamps(afk_events)
    idle_gap_s = 5 * 60
    min_seg_s = 60
    nt = noise_titles()

    evs: list[tuple[float, float, str, str, str]] = []
    for e in window_events or []:
        if not isinstance(e, dict):
            continue
        data = e.get("data") or {}
        app = str(data.get("app") or "").strip() or "unknown"
        title_raw = str(data.get("title") or "").strip()
        title = _clean_title(app, title_raw)
        if title.lower().strip() in nt or title_raw.lower().strip() in nt:
            continue
        t0 = _parse_event_dt(str(e.get("timestamp") or ""))
        if t0 is None:
            continue
        dur = float(e.get("duration") or 0.0)
        t1 = t0 + timedelta(seconds=dur)
        evs.append((t0.timestamp(), t1.timestamp(), app, title, title_raw))

    evs.sort(key=lambda x: x[0])

    out: list[dict] = []
    cur: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal cur
        if not cur:
            return
        st = cur["start"]
        en = cur["end"]
        if (en - st).total_seconds() < min_seg_s:
            cur = None
            return
        start_iso = _iso(st)
        end_iso = _iso(en)
        app = str(cur["app"])
        title = str(cur["title"])
        title_raw = str(cur.get("title_raw") or "")
        url = title_url.get(title_raw, "") or title_url.get(title, "")
        seg_id = _stable_segment_id(start_iso, end_iso, app, title)
        out.append(
            {
                "id": seg_id,
                "start": start_iso,
                "end": end_iso,
                "duration_minutes": round((en - st).total_seconds() / 60.0, 3),
                "app": app,
                "title": title,
                "url": url,
            }
        )
        cur = None

    last_end: float | None = None
    for t0, t1, app, title, title_raw in evs:
        if cur is None:
            cur = {
                "start": datetime.fromtimestamp(t0, tz=timezone.utc),
                "end": datetime.fromtimestamp(t1, tz=timezone.utc),
                "app": app,
                "title": title,
                "title_raw": title_raw,
            }
            last_end = t1
            continue

        gap = t0 - float(cur["end"].timestamp())
        same = (cur["app"] == app) and (cur["title"] == title)
        gap_ok = gap <= idle_gap_s and not _has_cut_between(cuts, float(cur["end"].timestamp()), t0)

        if same and gap_ok:
            cur["end"] = datetime.fromtimestamp(t1, tz=timezone.utc)
            cur["title_raw"] = title_raw  # keep latest raw title for URL mapping
            last_end = t1
            continue

        flush()
        cur = {
            "start": datetime.fromtimestamp(t0, tz=timezone.utc),
            "end": datetime.fromtimestamp(t1, tz=timezone.utc),
            "app": app,
            "title": title,
            "title_raw": title_raw,
        }
        last_end = t1

    flush()

    # Split any segment that spans an idle cut (AFK gap / long AFK) into multiple parts.
    split_out: list[dict] = []
    for seg in out:
        st = _parse_event_dt(str(seg.get("start") or ""))
        en = _parse_event_dt(str(seg.get("end") or ""))
        if not st or not en:
            continue
        inner_cuts = [c for c in cuts if st.timestamp() < c < en.timestamp()]
        if not inner_cuts:
            split_out.append(seg)
            continue
        inner_cuts.sort()
        cursor = st
        for c in inner_cuts:
            cdt = datetime.fromtimestamp(c, tz=timezone.utc)
            if (cdt - cursor).total_seconds() >= min_seg_s:
                s0 = _iso(cursor)
                s1 = _iso(cdt)
                split_out.append(
                    {
                        "id": _stable_segment_id(s0, s1, seg["app"], seg["title"]),
                        "start": s0,
                        "end": s1,
                        "duration_minutes": round((cdt - cursor).total_seconds() / 60.0, 3),
                        "app": seg["app"],
                        "title": seg["title"],
                        "url": seg.get("url") or "",
                    }
                )
            cursor = cdt
        if (en - cursor).total_seconds() >= min_seg_s:
            s0 = _iso(cursor)
            s1 = _iso(en)
            split_out.append(
                {
                    "id": _stable_segment_id(s0, s1, seg["app"], seg["title"]),
                    "start": s0,
                    "end": s1,
                    "duration_minutes": round((en - cursor).total_seconds() / 60.0, 3),
                    "app": seg["app"],
                    "title": seg["title"],
                    "url": seg.get("url") or "",
                }
            )

    split_out = [s for s in split_out if float(s.get("duration_minutes") or 0.0) * 60.0 >= min_seg_s]
    split_out.sort(key=lambda s: str(s.get("start") or ""))

    def _remove_overlapping_segments(segments: list[dict]) -> list[dict]:
        """
        Sort by start time.
        Walk through segments — if a segment overlaps with the previous one, trim or drop it.

        Priority: longer segments win over shorter ones within the same time window.
        """
        if not segments:
            return []

        sorted_segs = sorted(
            segments,
            key=lambda s: (str(s.get("start") or ""), -float(s.get("duration_minutes") or 0.0)),
        )

        result: list[dict] = []
        last_end: datetime | None = None

        for seg in sorted_segs:
            seg_start = _parse_iso_dt(str(seg.get("start") or ""))
            seg_end = _parse_iso_dt(str(seg.get("end") or ""))
            if seg_start is None or seg_end is None:
                continue

            if last_end is None or seg_start >= last_end:
                result.append(seg)
                last_end = seg_end
                continue

            if seg_end > last_end:
                trimmed_minutes = (seg_end - last_end).total_seconds() / 60.0
                if trimmed_minutes >= 1.0:
                    trimmed = dict(seg)
                    start_iso = _iso(last_end)
                    end_iso = _iso(seg_end)
                    trimmed["start"] = start_iso
                    trimmed["end"] = end_iso
                    trimmed["duration_minutes"] = round(trimmed_minutes, 3)
                    trimmed["id"] = _stable_segment_id(
                        start_iso,
                        end_iso,
                        str(trimmed.get("app") or ""),
                        str(trimmed.get("title") or ""),
                    )
                    result.append(trimmed)
                    last_end = seg_end
                continue
            # else: completely contained — drop it

        result.sort(key=lambda s: str(s.get("start") or ""))
        return result

    split_out = _remove_overlapping_segments(split_out)
    return split_out
