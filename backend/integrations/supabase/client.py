"""
Supabase client for reading ActivityChunk, ActivityChunkEntry, DailyInputStats,
WorkSession, and User tables.
Uses service role key to read all users' data.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import config  # noqa: F401 — loads repo-root .env

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
    """Returns WorkSession rows for a user on a given workDate."""
    client = get_client()
    result = (
        client.table("WorkSession")
        .select("id, workDate, loginAtMs, logoutAtMs, userId, deviceId")
        .eq("userId", user_id)
        .eq("workDate", date_str)
        .execute()
    )
    return result.data or []


def fetch_chunks_for_recording(recording_id: str) -> list[dict[str, Any]]:
    """Returns all ActivityChunk rows for a work session, ordered by seq (recording_id is workSessionId)."""
    client = get_client()
    result = (
        client.table("ActivityChunk")
        .select(
            "id, workSessionId, userId, seq, startMs, endMs, "
            "taskId, activeMs, afkMs, hasBrowserData, capturedAtMs"
        )
        .eq("workSessionId", recording_id)
        .order("seq")
        .execute()
    )
    return result.data or []


def fetch_chunk_entries(chunk_id: str) -> list[dict[str, Any]]:
    """Returns ActivityChunkEntry rows for a chunk."""
    client = get_client()
    result = (
        client.table("ActivityChunkEntry")
        .select("id, chunkId, kind, app, title, url, durationMs")
        .eq("chunkId", chunk_id)
        .execute()
    )
    return result.data or []


def fetch_chunks_for_user_date(user_id: str, date_str: str) -> list[dict[str, Any]]:
    """
    Convenience: fetch all chunks for a user on a date.
    Joins through WorkSession (workDate).
    """
    sessions = fetch_recordings_for_user(user_id, date_str)
    if not sessions:
        return []
    all_chunks: list[dict[str, Any]] = []
    for rec in sessions:
        chunks = fetch_chunks_for_recording(rec["id"])
        for ch in chunks:
            cid = ch.get("id")
            if cid is not None:
                ch = dict(ch)
                ch["entries"] = fetch_chunk_entries(str(cid))
            else:
                ch = dict(ch)
                ch["entries"] = []
            all_chunks.append(ch)
    all_chunks.sort(key=lambda c: c.get("seq", 0))
    return all_chunks


def fetch_input_activity(user_id: str, date_str: str) -> dict[str, Any] | None:
    """Returns aggregated DailyInputStats for a user on a date."""
    client = get_client()
    result = client.table("DailyInputStats").select("*").eq("userId", user_id).eq("date", date_str).execute()
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
