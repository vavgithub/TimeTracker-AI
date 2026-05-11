import config  # noqa: F401  # loads repo-root .env safely via python-dotenv
from integrations.supabase.client import (
    fetch_all_users,
    fetch_chunks_for_user_date,
    fetch_input_activity,
    fetch_recordings_for_user,
)
from integrations.supabase.adapter import chunks_to_sessions, input_activity_to_daily

print("=== USERS ===")
users = fetch_all_users()
for u in users:
    print(f"  {u['email']} ({u['id']})")

if users:
    user = users[0]
    date = "2026-05-06"
    print(f"\n=== WORK SESSIONS for {user['email']} on {date} ===")
    sessions = fetch_recordings_for_user(user["id"], date)
    for ws in sessions:
        print(
            f"  workDate={ws.get('workDate')} loginAtMs={ws.get('loginAtMs')} "
            f"logoutAtMs={ws.get('logoutAtMs')} id={ws.get('id')}"
        )

    print(f"\n=== CHUNKS for {user['email']} on {date} ===")
    chunks = fetch_chunks_for_user_date(user["id"], date)
    print(f"  {len(chunks)} chunks found")

    if chunks:
        print("\n=== SESSIONS from chunks ===")
        sessions_from_chunks = chunks_to_sessions(chunks)
        for s in sessions_from_chunks:
            print(f"  {s['start']} → {s['end']} ({s['duration_min']:.1f}m) apps={s['apps'][:2]} titles={s['titles'][:1]}")

    print(f"\n=== INPUT ACTIVITY ===")
    input_day = fetch_input_activity(user["id"], date)
    print(f"  {input_activity_to_daily(input_day)}")
