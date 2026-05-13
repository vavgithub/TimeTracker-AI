"""
Railway/Render cron worker.
Runs every 30 minutes.
Fetches all users from Supabase and runs the pipeline for each.

Skill profile (daily rollup) is written and pushed inside ``run_for_user`` whenever
``push=True`` — same cadence as daily summary, not weekday-gated.
"""

from __future__ import annotations

import datetime
import os
import sys

# Ensure backend/ is on path
sys.path.insert(0, os.path.dirname(__file__))

import config  # noqa: F401  # loads .env

from integrations.supabase.client import fetch_all_users
from pipeline.supabase_runner import run_for_user


def get_date_str() -> str:
    """Returns today's date in IST (UTC+5:30)."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    ist_offset = datetime.timedelta(hours=5, minutes=30)
    now_ist = now_utc + ist_offset
    return now_ist.strftime("%Y-%m-%d")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--user-id', type=str, default=None)
    parser.add_argument('--date', type=str, default=None)
    parser.add_argument('--eod-only', action='store_true', default=False)
    args = parser.parse_args()

    date_str = args.date or get_date_str()
    print(f"[worker] starting run for date={date_str}")

    users = fetch_all_users()
    print(f"[worker] found {len(users)} users")

    if not users:
        print("[worker] no users found — exiting")
        return

    results = []
    if args.eod_only:
        target_id = (args.user_id or "").strip()
        if target_id:
            user = next((u for u in users if str(u.get("id") or "") == target_id), None)
            if not user:
                print(f"[worker] user not found: {target_id}")
                return
            users = [user]

    for user in users:
        user_id = user.get("id", "")
        user_email = user.get("email", "")
        if not user_id or not user_email:
            print(f"[worker] skipping invalid user: {user}")
            continue

        try:
            result = run_for_user(
                user_id=user_id,
                user_email=user_email,
                date_str=date_str,
                post_eod=bool(args.eod_only),
                push=True,
            )
            results.append(result)
            print(f"[worker] ✓ {user_email}: {result}")
        except Exception as e:
            print(f"[worker] ✗ {user_email}: {e}")
            results.append({"status": "error", "user": user_email, "error": str(e)})

    ok = sum(1 for r in results if r.get("status") == "ok")
    skip = sum(1 for r in results if r.get("status") == "no_data")
    err = sum(1 for r in results if r.get("status") == "error")
    print(f"[worker] done — ok={ok} skipped={skip} errors={err}")


if __name__ == "__main__":
    main()

