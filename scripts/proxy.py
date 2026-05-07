#!/usr/bin/env python3
"""
Trace dev proxy: CORS-friendly ActivityWatch + static out/*.json for the Trace dashboard.

  http://localhost:5899/aw/<path>   → http://localhost:5600/api/0/<path>
  http://localhost:5899/out/<file>  → <repo>/out/<file>

Run: python3 scripts/proxy.py   (or repo-root symlink ./proxy.py → scripts/proxy.py)
"""
from __future__ import annotations

import os
import json
from datetime import datetime, timezone
import threading
import time
import subprocess
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

_here = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_here) if os.path.basename(_here) == "scripts" else _here
OUT_ROOT = os.path.normpath(os.path.join(REPO_ROOT, "out"))

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(REPO_ROOT, ".env"))
except ImportError:
    pass

_autosync_state = {"last_run": None, "last_exit_code": None, "running": False}

def _get_clickup_token() -> str:
    """
    Resolve ClickUp token from env first, then fallback to local .env.
    This keeps `proxy.py` usable when started via nohup/systemd without inheriting
    your interactive shell exports.
    """
    token = (os.getenv("CLICKUP_TOKEN", "") or "").strip()
    if token:
        return token

    env_path = os.path.join(REPO_ROOT, ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() != "CLICKUP_TOKEN":
                    continue
                val = v.strip().strip('"').strip("'")
                return val
    except OSError:
        pass

    return ""


def _cors(handler: BaseHTTPRequestHandler) -> None:
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")


class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args) -> None:
        return

    def _write_body(self, data: bytes) -> None:
        """Ignore client disconnect (common on large /clickup/tasks JSON)."""
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        _cors(self)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        query = parsed.query
        qs = parse_qs(query or "")

        if path == "/scheduler/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            _cors(self)
            self.end_headers()
            self._write_body(json.dumps(_autosync_state).encode())
            return

        if path == "/clickup/tasks":
            token = _get_clickup_token()
            if not token:
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                _cors(self)
                self.end_headers()
                self._write_body(
                    json.dumps(
                        {
                            "error": "CLICKUP_TOKEN missing",
                            "hint": "export CLICKUP_TOKEN=... then retry",
                        }
                    ).encode()
                )
                return

            # Defaults match legacy proxy; override in .env for other workspaces/users.
            team_id = (os.getenv("CLICKUP_TEAM_ID", "") or "9016313867").strip()
            assignee_id = (os.getenv("CLICKUP_ASSIGNEE_ID", "") or "100975806").strip()

            # NOTE: Do not status-filter at the API layer.
            # Subtasks often have different statuses than their parent, and filtering here
            # breaks the 3-level tree in the UI by dropping children.
            page_limit = 100
            merged: list[dict] = []
            page = 0
            try:
                while True:
                    q: list[tuple[str, str]] = [
                        ("assignees[]", assignee_id),
                        ("subtasks", "true"),
                        ("depth", "3"),
                        ("include_subtasks", "true"),
                        ("include_closed", "false"),
                        ("page", str(page)),
                    ]
                    api_url = f"https://api.clickup.com/api/v2/team/{team_id}/task?{urlencode(q)}"
                    req = urllib.request.Request(api_url, headers={"Authorization": token})
                    with urllib.request.urlopen(req, timeout=60) as r:
                        raw = json.loads(r.read())
                    batch = raw.get("tasks", []) or []
                    merged.extend(batch)
                    if len(batch) < page_limit:
                        break
                    page += 1
                    if page > 100:
                        break

                def _flatten_clickup_tasks(tasks: list) -> list[dict]:
                    seen: set[str] = set()
                    out: list[dict] = []

                    def visit(t: dict, parent_id: str | None = None) -> None:
                        tid = t.get("id")
                        if tid is None:
                            return
                        sid = str(tid)
                        if sid in seen:
                            return
                        seen.add(sid)
                        # Ensure flattened subtasks preserve a usable parent ID for UI tree building.
                        if parent_id is not None and not t.get("parent"):
                            t["parent"] = parent_id
                        out.append(t)
                        for st in t.get("subtasks") or []:
                            if isinstance(st, dict):
                                visit(st, parent_id=sid)

                    for t in tasks:
                        if isinstance(t, dict):
                            visit(t)
                    return out

                flat = _flatten_clickup_tasks(merged)

                result = []
                for t in flat:
                    try:
                        par = t.get("parent")
                        if isinstance(par, dict):
                            par = par.get("id")
                        result.append(
                            {
                                "id": t["id"],
                                "name": t["name"],
                                "status": (t.get("status") or {}).get("status", ""),
                                "parent": par,
                                "list": (t.get("list") or {}).get("name", ""),
                                "url": t.get("url", ""),
                            }
                        )
                    except Exception:
                        continue

                out = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                _cors(self)
                self.end_headers()
                self._write_body(out)
            except urllib.error.HTTPError as e:
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                _cors(self)
                self.end_headers()
                try:
                    body = e.read().decode("utf-8", "ignore")
                except Exception:
                    body = ""
                self._write_body(
                    json.dumps(
                        {
                            "error": "ClickUp API error",
                            "status": int(getattr(e, "code", 0) or 0),
                            "reason": str(getattr(e, "reason", "")),
                            "body": body,
                        }
                    ).encode()
                )
            except Exception as e:
                self.send_response(500)
                _cors(self)
                self.end_headers()
                self._write_body(str(e).encode())
            return

        if path.startswith("/aw/"):
            tail = path[len("/aw/") :].lstrip("/")
            aw_url = f"http://localhost:5600/api/0/{tail}"
            if query:
                aw_url = f"{aw_url}?{query}"
            try:
                req = urllib.request.Request(aw_url, headers={"User-Agent": "trace-proxy"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                _cors(self)
                self.end_headers()
                self._write_body(data)
            except (urllib.error.URLError, OSError, TimeoutError):
                self.send_response(503)
                _cors(self)
                self.end_headers()
            return

        if path.startswith("/out/"):
            rel = path[len("/out/") :].lstrip("/")
            if not rel or ".." in rel.split(os.sep):
                self.send_response(400)
                _cors(self)
                self.end_headers()
                return
            filepath = os.path.normpath(os.path.join(OUT_ROOT, rel))
            if not filepath.startswith(OUT_ROOT + os.sep) and filepath != OUT_ROOT:
                self.send_response(403)
                _cors(self)
                self.end_headers()
                return
            if not os.path.isfile(filepath):
                self.send_response(404)
                _cors(self)
                self.end_headers()
                return
            with open(filepath, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            _cors(self)
            self.end_headers()
            self._write_body(data)
            return

        if path == "/sessions":
            date = (qs.get("date", [""]) or [""])[0].strip()
            if not date:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                _cors(self)
                self.end_headers()
                self._write_body(json.dumps({"error": "Missing date (YYYY-MM-DD)"}).encode())
                return
            filename = f"sessions_{date}.json"
            filepath = os.path.normpath(os.path.join(OUT_ROOT, filename))
            if not filepath.startswith(OUT_ROOT + os.sep) and filepath != OUT_ROOT:
                self.send_response(403)
                _cors(self)
                self.end_headers()
                return
            if not os.path.isfile(filepath):
                self.send_response(404)
                _cors(self)
                self.end_headers()
                return
            with open(filepath, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            _cors(self)
            self.end_headers()
            self._write_body(data)
            return

        if path == "/segments":
            date = (qs.get("date", [""]) or [""])[0].strip()
            if not date:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                _cors(self)
                self.end_headers()
                self._write_body(json.dumps({"error": "Missing date (YYYY-MM-DD)"}).encode())
                return
            filename = f"segments_{date}.json"
            filepath = os.path.normpath(os.path.join(OUT_ROOT, filename))
            if not filepath.startswith(OUT_ROOT + os.sep) and filepath != OUT_ROOT:
                self.send_response(403)
                _cors(self)
                self.end_headers()
                return
            if not os.path.isfile(filepath):
                self.send_response(404)
                _cors(self)
                self.end_headers()
                return
            with open(filepath, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            _cors(self)
            self.end_headers()
            self._write_body(data)
            return

        self.send_response(404)
        _cors(self)
        self.end_headers()


if __name__ == "__main__":
    port = int(os.environ.get("TRACE_PROXY_PORT", "5899"))
    autosync = (os.environ.get("TRACE_AUTOSYNC", "1") or "").strip().lower() not in ("0", "false", "no")
    autosync_seconds = int(os.environ.get("TRACE_AUTOSYNC_SECONDS", "120"))

    if autosync:
        def _run_once():
            _autosync_state["running"] = True
            try:
                venv_py = os.path.join(REPO_ROOT, ".venv", "bin", "python3")
                py = venv_py if os.path.isfile(venv_py) else sys.executable
                cmd = [py, os.path.join(REPO_ROOT, "main.py"), "--write-out"]
                p = subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                _autosync_state["last_exit_code"] = int(p.returncode)
            except Exception:
                _autosync_state["last_exit_code"] = -1
            finally:
                _autosync_state["last_run"] = datetime.now(timezone.utc).isoformat()
                _autosync_state["running"] = False

        def _loop():
            _run_once()
            while True:
                time.sleep(max(10, autosync_seconds))
                _run_once()

        t = threading.Thread(target=_loop, daemon=True)
        t.start()

    server = HTTPServer(("127.0.0.1", port), ProxyHandler)
    print(f"Trace proxy http://127.0.0.1:{port}")
    print(f"  AW:   http://127.0.0.1:{port}/aw/buckets/…")
    print(f"  JSON: http://127.0.0.1:{port}/out/daily_YYYY-MM-DD.json")
    server.serve_forever()
