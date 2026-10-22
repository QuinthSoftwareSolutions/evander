"""Read-only coordinated-cutover check. Does not edit live configuration or grants."""

import argparse
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote


def rows(path, table):
    if not path.exists():
        return []
    with sqlite3.connect("file:" + quote(str(path.resolve())) + "?mode=ro", uri=True) as db:
        return [json.loads(row[0]) for row in db.execute("SELECT payload FROM " + table)]


def check(runtime_dir, worker_states=(), sql_profiles=None):
    runtime_dir = Path(runtime_dir)
    path = runtime_dir / "addons/browser-sessions-addon/state.db"
    sessions = rows(path, "sessions")
    blockers = [
        "Legacy hosted resource requires release: " + session["id"]
        for session in sessions
        if session["mode"] in {"hosted_demo", "hosted_development"}
        and session["state"] not in {"closed", "expired"}
    ]
    worker_states = dict(worker_states)
    for worker_id, worker in worker_states.items():
        worker_path = Path(worker) / "browser-sessions/leases.db"
        if not worker_path.is_file():
            blockers.append("Worker state snapshot is missing: " + worker_id)
            continue
        for lease in rows(worker_path, "leases"):
            if lease.get("session", {}).get("worker_id") != worker_id:
                blockers.append("Worker state snapshot identity differs: " + worker_id)
            if lease["state"] != "closed":
                blockers.append("Worker resource requires verified cleanup: " + lease["id"])
    dependencies = runtime_dir / "session-dependencies.db"
    if dependencies.exists():
        legacy_ids = {
            session["id"]
            for session in sessions
            if session["mode"] in {"hosted_demo", "hosted_development"}
        }
        with sqlite3.connect(
            "file:" + quote(str(dependencies.resolve())) + "?mode=ro", uri=True
        ) as db:
            for row in db.execute("SELECT id FROM dependencies"):
                if row[0].split(":", 1)[0] in legacy_ids:
                    blockers.append("Legacy checkout/resource dependency remains: " + row[0])
    settings = []
    if path.exists():
        with sqlite3.connect("file:" + quote(str(path.resolve())) + "?mode=ro", uri=True) as db:
            settings = [
                (row[0], row[1], json.loads(row[2]))
                for row in db.execute("SELECT directory,revision,payload FROM settings")
            ]
    proposals = []
    sql_profiles = sql_profiles or {}
    for directory, revision, old in settings:
        browser = {key: value for key, value in old.items() if key != "profiles"}
        browser["profiles"] = {}
        applications = {"allocations_enabled": False, "profiles": {}, "workers": {}}
        for id, profile in old.get("profiles", {}).items():
            if "browser_image" not in profile:
                browser["profiles"][id] = profile
                continue
            browser["profiles"][id] = {
                "image": profile["browser_image"],
                "cpus": 1,
                "memory_mib": profile.get("browser_memory_mib", 1536),
                "readonly_mounts": profile.get("readonly_mounts", {}),
                "playwright_enabled": False,
            }
            app = {
                key: value
                for key, value in profile.items()
                if key not in {"browser_image", "browser_memory_mib", "database_enabled"}
            }
            if profile.get("database_enabled"):
                if id not in sql_profiles:
                    blockers.append("Map the SQL profile for application profile: " + id)
                app["database_profile"] = sql_profiles.get(id)
            applications["profiles"][id] = app
        missing_workers = set(old.get("workers", {})) - set(worker_states)
        for worker_id in sorted(missing_workers):
            blockers.append(
                "Supply a worker-state snapshot before cutover: " + worker_id + " in " + directory
            )
        proposals.append(
            {
                "directory": directory,
                "expected_browser_revision": revision,
                "browser_settings": browser,
                "application_settings": applications,
                "application_workers_required": list(old.get("workers", {})),
            }
        )
    return {
        "ready": not blockers,
        "blockers": blockers,
        "proposals": proposals,
        "required_grant_changes": {
            "remove": ["browser_session_workspaces"],
            "application_tools": [
                "application_environment_profiles",
                "application_environment_workspaces",
                "application_environment_create",
                "application_environment_status",
                "application_environment_close",
            ],
            "optional_browser_tools": ["browser_session_connect", "browser_session_authenticate"],
        },
        "notes": [
            "Configure application worker origins, separate service tokens and SQL worker resource profiles before enabling allocations.",
            "Apply configuration through revision-checked Admin APIs; this checker makes no changes.",
            "Keep legacy daemons and secrets available until all resource and checkout guards are released.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--worker-state", action="append", default=[], metavar="WORKER_ID=PATH")
    parser.add_argument(
        "--sql-profile", action="append", default=[], metavar="APPLICATION_PROFILE=SQL_PROFILE"
    )
    args = parser.parse_args()
    mappings = {}
    for value in args.sql_profile:
        key, separator, target = value.partition("=")
        if not key or not separator or not target:
            parser.error("Expected APPLICATION_PROFILE=SQL_PROFILE")
        mappings[key] = target
        # Keys prove coverage of every configured placement; duplicate paths/counts do not.
    worker_states = {}
    for value in args.worker_state:
        key, separator, path = value.partition("=")
        if not key or not separator or not path or key in worker_states:
            parser.error("Expected a unique WORKER_ID=PATH")
        worker_states[key] = Path(path)
    result = check(args.runtime_dir, worker_states, mappings)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ready"] else 2)
