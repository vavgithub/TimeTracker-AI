import os
from datetime import datetime, timezone

import requests


BASE_URL = "https://api.clickup.com/api/v2"


def resolve_clickup_api_token(stored_user_token: str | None) -> str | None:
    """
    Value from User.clickupAccessToken: only use as the API Authorization header when
    it looks like a ClickUp personal API token (``pk_...``). Hashes and other opaque
    placeholders are ignored so ``CLICKUP_TOKEN`` from the environment is used.

    Set ``CLICKUP_TRUST_DB_TOKEN=1`` to pass through any non-empty stored string
    (e.g. if you store a non-pk_ secret and accept the risk).
    """
    trust = (os.getenv("CLICKUP_TRUST_DB_TOKEN") or "").strip().lower() in ("1", "true", "yes")
    t = (stored_user_token or "").strip()
    if not t:
        return None
    if trust:
        return t
    if t.startswith("pk_"):
        return t
    return None


class ClickUpClient:
    def __init__(self, token: str | None = None):
        self.token = token or os.getenv("CLICKUP_TOKEN") or ""

    def _headers(self):
        return {"Authorization": self.token}

    def _get(self, path: str, params: dict | None = None):
        r = requests.get(f"{BASE_URL}{path}", headers=self._headers(), params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_teams(self):
        return self._get("/team").get("teams", [])

    def get_user(self):
        return self._get("/user").get("user")

    def get_user_role(self, user_id: str | None = None) -> str:
        """
        Fetch role / job title from the authenticated ClickUp user profile (GET /user).
        ``user_id`` is accepted for API symmetry; the v2 ``/user`` endpoint returns the token user only.
        """
        _ = user_id
        try:
            resp = self._get("/user")
            user = resp.get("user") or {}
            if not isinstance(user, dict):
                return "general"
            role = user.get("title") or user.get("role") or user.get("job_title") or "general"
            s = str(role).strip()
            return s if s else "general"
        except Exception as e:
            print(f"[clickup] Could not fetch user role: {e}")
            return "general"

    def get_team_members(self):
        teams = self.get_teams()
        members = {}
        for t in teams:
            for m in t.get("members", []):
                u = m.get("user", {})
                uid = u.get("id")
                if uid is None:
                    continue
                members[uid] = {
                    "id": uid,
                    "name": u.get("username") or u.get("name") or "",
                    "email": u.get("email") or "",
                    "team_id": t.get("id"),
                }
        return members

    def get_tasks_for_user(
        self,
        team_id: str | int,
        user_id: str | int,
        statuses: list[str] | None = None,
        include_closed: bool = False,
        *,
        subtasks: bool = True,
    ):
        params = {
            "assignees[]": user_id,
            "include_closed": str(include_closed).lower(),
            "subtasks": str(subtasks).lower(),
        }
        if statuses:
            # requests will encode list parameters as repeated keys
            params["statuses[]"] = statuses
        data = self._get(f"/team/{team_id}/task", params=params)
        return data.get("tasks", [])

    def get_task(self, task_id: str | int, *, include_subtasks: bool = True) -> dict:
        """Single task; include_subtasks hydrates nested subtasks when API returns ID-only refs."""
        params = {"include_subtasks": str(include_subtasks).lower()}
        return self._get(f"/task/{task_id}", params=params)

    def expand_assignee_tasks_with_nested_subtasks(
        self,
        tasks: list | None,
        *,
        max_depth: int = 3,
    ) -> list[dict]:
        """
        Flatten team assignee tasks plus nested subtask trees for keyword matching.
        Fetches GET /task/{{id}} when a subtask entry is an ID string.
        Stops at max_depth (0 = root tasks only, no subtask recursion).
        """
        if not tasks:
            return []

        out: list[dict] = []
        seen: set[str] = set()

        def _tid(t: dict) -> str | None:
            tid = t.get("id")
            return str(tid) if tid is not None else None

        def _add(node: dict) -> None:
            tid = _tid(node)
            if not tid or tid in seen:
                return
            seen.add(tid)
            out.append(node)

        def _walk(node: dict, depth: int = 0) -> None:
            if not isinstance(node, dict):
                return
            _add(node)
            if depth >= max_depth:
                return
            for st in node.get("subtasks") or []:
                if isinstance(st, dict) and st.get("id"):
                    _walk(st, depth + 1)
                elif isinstance(st, (str, int)):
                    try:
                        detail = self.get_task(st, include_subtasks=True)
                        _walk(detail, depth + 1)
                    except Exception:
                        continue

        for t in tasks:
            _walk(t, 0)

        return out

    def get_team_tasks_updated_between(
        self,
        team_id: str | int,
        user_id: str | int,
        start_ms: int,
        end_ms: int,
    ) -> list[dict]:
        """
        Tasks updated in [start_ms, end_ms) (ms epoch), assigned to user.
        Same filter as Trace proxy /clickup/tasks — catches batch work not in strict in-progress/open.
        """
        params = {
            "date_updated_gt": start_ms,
            "date_updated_lt": end_ms,
            "subtasks": "true",
            "include_closed": "true",
            "assignees[]": user_id,
        }
        data = self._get(f"/team/{team_id}/task", params=params)
        return data.get("tasks", []) or []

    def get_time_entries(
        self,
        team_id: str | int,
        user_id: str | int,
        start_ms: int,
        end_ms: int,
    ):
        """
        Returns ClickUp time entries in the given time range (ms since epoch).
        """
        params = {
            "start_date": start_ms,
            "end_date": end_ms,
            "assignee": user_id,
        }
        data = self._get(f"/team/{team_id}/time_entries", params=params)
        return data.get("data", []) or data.get("time_entries", []) or []

    # --- Hierarchy discovery (python -m integrations.clickup.discovery) ---
    # Docs: GET /team/{team_id}/space, /space/{id}/folder, /folder/{id}/list, /list/{id}/task

    def get_spaces(self, team_id: str | int, archived: bool = False):
        return self._get(f"/team/{team_id}/space", params={"archived": str(archived).lower()}).get("spaces", [])

    def get_folders(self, space_id: str | int, archived: bool = False):
        return self._get(f"/space/{space_id}/folder", params={"archived": str(archived).lower()}).get("folders", [])

    def get_lists_in_folder(self, folder_id: str | int, archived: bool = False):
        return self._get(f"/folder/{folder_id}/list", params={"archived": str(archived).lower()}).get("lists", [])

    def get_lists_in_space(self, space_id: str | int, archived: bool = False):
        """Lists not inside a folder (folderless)."""
        return self._get(f"/space/{space_id}/list", params={"archived": str(archived).lower()}).get("lists", [])

    def get_list_tasks(
        self,
        list_id: str | int,
        *,
        include_closed: bool = True,
        subtasks: bool = True,
        archived: bool = False,
    ):
        """
        Tasks in a list. Use subtasks=true so weekly Call parents include embedded subtasks.
        """
        params = {
            "archived": str(archived).lower(),
            "include_closed": str(include_closed).lower(),
            "subtasks": str(subtasks).lower(),
        }
        return self._get(f"/list/{list_id}/task", params=params).get("tasks", [])


def flatten_calls_list_tasks(tasks: list) -> list[dict]:
    """
    Build match targets from Calls list: prefer subtasks (Standup, Workshop, …).
    Each item: {id, name, parent_name}.
    """
    targets: list[dict] = []
    for t in tasks or []:
        parent_name = t.get("name")
        subs = t.get("subtasks") or []
        if subs:
            for st in subs:
                targets.extend(_walk_subtasks(st, parent_name))
        else:
            tid = t.get("id")
            if tid is not None:
                targets.append({"id": str(tid), "name": t.get("name") or "", "parent_name": None})
    return targets


def _walk_subtasks(task: dict, parent_name: str | None) -> list[dict]:
    out = []
    tid = task.get("id")
    if tid is not None:
        out.append({"id": str(tid), "name": task.get("name") or "", "parent_name": parent_name})
    for st in task.get("subtasks") or []:
        out.extend(_walk_subtasks(st, task.get("name") or parent_name))
    return out


def ms_to_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return None

