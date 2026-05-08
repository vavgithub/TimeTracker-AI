"""Backend settings and repo-root paths (import this before other backend modules need env vars)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_ROOT.parent
OUTPUT_DIR = REPO_ROOT / "out"

load_dotenv(REPO_ROOT / ".env")

import json, tempfile, os

gcp_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
if gcp_json and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    tmp.write(gcp_json)
    tmp.flush()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = tmp.name

USER_EMAIL = "khyathiatvav@gmail.com"
HOSTNAME = "khyathi-Inspiron-14-7445-2-in-1"

CLICKUP_TEAM_ID = os.getenv("CLICKUP_TEAM_ID", "")
CLICKUP_USER_EMAIL = os.getenv("CLICKUP_USER_EMAIL", USER_EMAIL)
# Used in EOD task categorisation (Vertex prompt); e.g. designer, developer, pm.
EMPLOYEE_ROLE = (os.getenv("EMPLOYEE_ROLE", "general") or "general").strip() or "general"
# Optional: ClickUp list ID for mapping meeting titles → task names (Standup, Workshop, …).
# Scheduled call times come from Google Calendar (HR), not from walking ClickUp spaces/Calls.
CLICKUP_CALLS_LIST_ID = os.getenv("CLICKUP_CALLS_LIST_ID", "").strip()

BASE_URL = "http://localhost:5600/api/0"

WEB_BUCKETS = [
    "aw-watcher-web-brave_khyathi-Inspiron-14-7445-2-in-1",
    "aw-watcher-web-chrome_khyathi-Inspiron-14-7445-2-in-1",
]

WINDOW_BUCKET = "aw-watcher-window_khyathi-Inspiron-14-7445-2-in-1"
AFK_BUCKET = "aw-watcher-afk_khyathi-Inspiron-14-7445-2-in-1"
# Must match ActivityWatch UI → Raw data (aw-watcher-input is optional on many installs).
INPUT_BUCKET = f"aw-watcher-input_{HOSTNAME}"

SESSION_GAP_MINUTES = 15
APPLY_AFK_FILTER = False
MIN_DURATION = 300  # seconds
MERGE_GAP_MINUTES = 15
MAX_SESSION_MINUTES = 240

IGNORE_APPS = [
    "unknown",
    "Apport-gtk",
    "xdg-desktop-portal",
]

# Meeting detection (Google Calendar vs Zoom title). Substrings are matched case-insensitively on event title.
# Events here use calendar start/end and overlap rules in gcal/client.py.
CALENDAR_EXACT_EVENTS = [
    "standup",
    "retro",
    "client call",
    "hiring call",
    "syncup",
    "project review call",
]

# Calendar end time is unreliable — match start in slot and/or Zoom + title after slot (see gcal/client.py).
ZOOM_EXTENDED_EVENTS = [
    "workshop call",
]

# Days a typical Workshop Call runs; used to gate “extended past calendar” title-based matches.
WORKSHOP_CALL_DAYS = ["monday", "wednesday", "thursday"]
