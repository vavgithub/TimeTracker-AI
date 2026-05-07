#!/usr/bin/env python3
"""
Optional dev utility: walk ClickUp spaces/folders to find a list ID (e.g. for CLICKUP_CALLS_LIST_ID).
The time tracker does not call this at runtime; scheduled calls are planned via Google Calendar (HR).

Run with cwd set to backend/ (imports resolve as there):

  cd backend && python -m integrations.clickup.discovery
  python -m integrations.clickup.discovery --show-tasks
"""
from __future__ import annotations

import argparse
import sys
import config  # noqa: F401 — loads repo-root `.env`

from .client import ClickUpClient, flatten_calls_list_tasks


def _parse_args():
    p = argparse.ArgumentParser(description="Print ClickUp space/folder/list IDs for wiring config")
    p.add_argument("--team", default="", help="Substring to pick workspace (default: first team)")
    p.add_argument("--space", default="delivery", help="Substring to find space name (case-insensitive)")
    p.add_argument("--folder", default="vav internal", help="Substring to find folder under space")
    p.add_argument("--list", default="calls", help="Substring to find list named Calls")
    p.add_argument("--show-tasks", action="store_true", help="Fetch sample tasks + subtasks from Calls list")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    cu = ClickUpClient()
    if not cu.token:
        print("Set CLICKUP_TOKEN in .env or environment.", file=sys.stderr)
        return 1

    teams = cu.get_teams()
    if not teams:
        print("No teams returned — check token scopes.", file=sys.stderr)
        return 1

    team = None
    if args.team:
        low = args.team.lower()
        team = next((t for t in teams if low in (t.get("name") or "").lower()), None)
    team = team or teams[0]
    team_id = team["id"]
    print(f"Team: {team.get('name')} — id={team_id}\n")

    spaces = cu.get_spaces(team_id)
    s_low = args.space.lower()
    space = next((s for s in spaces if s_low in (s.get("name") or "").lower()), None)
    if not space:
        print("Spaces found:")
        for s in spaces:
            print(f"  - {s.get('name')} — id={s.get('id')}")
        print(f"\nNo space matching {args.space!r}", file=sys.stderr)
        return 1

    print(f"Space: {space.get('name')} — id={space['id']}")

    folders = cu.get_folders(space["id"])
    f_low = args.folder.lower()
    folder = next((f for f in folders if f_low in (f.get('name') or "").lower()), None)
    if not folder:
        print("Folders in space:")
        for f in folders:
            print(f"  - {f.get('name')} — id={f.get('id')}")
        print(f"\nNo folder matching {args.folder!r}", file=sys.stderr)
        return 1

    print(f"Folder: {folder.get('name')} — id={folder['id']}")

    lists = cu.get_lists_in_folder(folder["id"])
    l_low = args.list.lower()
    lst = next((x for x in lists if l_low in (x.get("name") or "").lower()), None)
    if not lst:
        print("Lists in folder:")
        for x in lists:
            print(f"  - {x.get('name')} — id={x.get('id')}")
        print(f"\nNo list matching {args.list!r}", file=sys.stderr)
        return 1

    print(f"List: {lst.get('name')} — id={lst['id']}")

    print("\n--- Add to .env ---")
    print(f"CLICKUP_TEAM_ID={team_id}")
    print(f"CLICKUP_CALLS_LIST_ID={lst['id']}")
    print("--- end ---\n")

    if args.show_tasks:
        tasks = cu.get_list_tasks(lst["id"])
        print(f"Tasks in list (include_closed): {len(tasks)}")
        for t in tasks[:8]:
            st = t.get("subtasks") or []
            print(f"  • {t.get('name')} — id={t.get('id')} — subtasks={len(st)}")
            for s in st[:12]:
                print(f"      └ {s.get('name')} — id={s.get('id')}")
        flat = flatten_calls_list_tasks(tasks)
        print(f"\nFlattened meeting targets (subtasks) for mapper: {len(flat)}")
        for row in flat[:15]:
            print(f"  - {row['name']} (parent: {row.get('parent_name')})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
