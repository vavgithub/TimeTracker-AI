"""
Supabase client for reading WorkActivityChunk, InputActivityDay,
WorkActivityRecording, and User tables.
Uses service role key to read all users' data.
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import config  # noqa: F401 — loads repo-root .env

import os
from typing import Any

from supabase import Client, create_client


def get_client() -> Client:
    url = (os.getenv("SUPABASE_URL", "") or "").strip()
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or "").strip()
    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
    return create_client(url, key)


def fetch_all_users() -> list[dict[str, Any]]:
    """Returns all users from the User table."""
    client = get_client()
    result = client.table("User").select("id, email, clickupUsername, timezone, clickupAccessToken").execute()
    return result.data or []


def fetch_recordings_for_user(user_id: str, date_str: str) -> list[dict[str, Any]]:
    """Returns WorkActivityRecording rows for a user on a given date."""
    client = get_client()
    result = client.table("WorkActivityRecording").select("*").eq("userId", user_id).eq("date", date_str).execute()
    return result.data or []


def fetch_chunks_for_recording(recording_id: str) -> list[dict[str, Any]]:
    """Returns all WorkActivityChunk rows for a recording, ordered by seq."""
    client = get_client()
    result = client.table("WorkActivityChunk").select("*").eq("recordingId", recording_id).order("seq").execute()
    return result.data or []


def fetch_chunks_for_user_date(user_id: str, date_str: str) -> list[dict[str, Any]]:
    """
    Convenience: fetch all chunks for a user on a date.
    Joins through WorkActivityRecording.
    """
    recordings = fetch_recordings_for_user(user_id, date_str)
    if not recordings:
        return []
    all_chunks: list[dict[str, Any]] = []
    for rec in recordings:
        chunks = fetch_chunks_for_recording(rec["id"])
        all_chunks.extend(chunks)
    all_chunks.sort(key=lambda c: c.get("seq", 0))
    return all_chunks


def fetch_input_activity(user_id: str, date_str: str) -> dict[str, Any] | None:
    """Returns InputActivityDay for a user on a date."""
    client = get_client()
    result = client.table("InputActivityDay").select("*").eq("userId", user_id).eq("day", date_str).execute()
    rows = result.data or []
    if not rows:
        return None
    # Sum across devices if multiple
    total_clicks = sum(r.get("clicks", 0) or 0 for r in rows)
    total_presses = sum(r.get("presses", 0) or 0 for r in rows)
    return {
        "userId": user_id,
        "day": date_str,
        "clicks": total_clicks,
        "presses": total_presses,
    }

