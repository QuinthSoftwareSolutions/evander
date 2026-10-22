"""Offline, explicitly reviewed migration of browser credential references.

The plan contains destinations and environment *names*, never secret values.
Runtime request handlers never call this module or interpret these selectors.
"""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from evander.credentials import CredentialError
from evander.session_dependencies import requires_browser_credential_migration

from .credentials import Credentials, connection_binding
from .models import Settings, WorkerConnection


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def inventory(runtime_dir):
    runtime = Path(runtime_dir).absolute()
    path = runtime / "addons/browser-sessions-addon/state.db"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        settings = {
            d: [r, json.loads(p)]
            for d, r, p in db.execute("SELECT directory,revision,payload FROM settings")
        }
        sessions = {i: json.loads(p) for i, p in db.execute("SELECT id,payload FROM sessions")}
        private = {
            i: json.loads(p)
            for i, p in db.execute("SELECT session_id,payload FROM private_sessions")
        }
    dep_path = runtime / "session-dependencies.db"
    dependencies = []
    if dep_path.exists():
        with closing(sqlite3.connect(dep_path.as_uri() + "?mode=ro", uri=True)) as db:
            cols = {r[1] for r in db.execute("PRAGMA table_info(dependencies)")}
            managed = "managed_auth" if "managed_auth" in cols else "NULL"
            dependencies = list(
                db.execute(
                    f"SELECT session_id,id,release_url,token_env,{managed} FROM dependencies ORDER BY id"
                )
            )
    source_hash = _digest([settings, sessions, private, dependencies])
    entries, changes, conflicts = {}, [], []

    def add(
        purpose, directory, url, environment, *, worker_id=None, connection=None, required=True
    ):
        if not isinstance(environment, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*", environment
        ):
            raise ValueError("Invalid legacy environment selector")
        if purpose == "worker":
            connection = dict(connection)
            connection.pop("token_env", None)
            connection["credential_ref"] = None
            connection = WorkerConnection.model_validate(connection)
        binding = connection_binding(purpose, url, worker_id=worker_id, connection=connection)
        value = {"directory": directory, "environment": environment, "binding": binding}
        identity = _digest(value)
        required = required or entries.get(identity, {}).get("required", False)
        entries[identity] = {"id": identity, **value, "required": required, "approved": False}
        return identity

    def fields(data, directory, kind, key, *, worker_id=None):
        if kind == "worker":
            if "token_env" in data:
                identity = add(
                    "worker",
                    directory,
                    data["base_url"],
                    data["token_env"],
                    worker_id=worker_id,
                    connection=data,
                )
                changes.append((key, data, "token_env", "credential_ref", identity))
        else:
            old, new, url = (
                ("relay_token_env", "relay_credential_ref", "relay_url")
                if kind == "relay"
                else ("api_key_env", "credential_ref", "api_base_url")
            )
            if old in data:
                identity = add(
                    kind,
                    directory,
                    data[url],
                    data[old],
                    required=kind != "voice" or bool(data.get("enabled", False)),
                )
                changes.append((key, data, old, new, identity))

    for directory, (_, value) in settings.items():
        try:
            fields(value, directory, "relay", "settings:" + directory)
            fields(value.get("voice", {}), directory, "voice", "settings:" + directory)
            for worker_id, connection in value.get("workers", {}).items():
                fields(
                    connection, directory, "worker", "settings:" + directory, worker_id=worker_id
                )
        except (KeyError, ValueError, TypeError):
            conflicts.append("Invalid legacy settings in " + directory)

    def effective(directory):
        candidates = [
            key for key in settings if directory == key or directory.startswith(key + "/")
        ]
        return settings[max(candidates, key=len)][1] if candidates else {}

    for session_id, session in sessions.items():
        value = private.setdefault(session_id, {})
        directory = session["directory"]
        try:
            fields(
                value.get("connection", {}),
                directory,
                "worker",
                "private:" + session_id,
                worker_id=session.get("worker_id"),
            )
            if "relay_url" not in value:
                config = effective(directory)
                if "relay_token_env" in config:
                    value.update(
                        relay_url=config["relay_url"], relay_token_env=config["relay_token_env"]
                    )
                elif config.get("relay_credential_ref"):
                    value.update(
                        relay_url=config["relay_url"],
                        relay_credential_ref=config["relay_credential_ref"],
                    )
            fields(value, directory, "relay", "private:" + session_id)
        except (KeyError, ValueError, TypeError):
            conflicts.append("Invalid legacy private connection for " + session_id)

    dep_changes = []
    for owner, dependency_id, url, environment, managed in dependencies:
        if managed or not requires_browser_credential_migration(owner, dependency_id, url):
            continue
        session_id = dependency_id.split(":", 1)[0]
        session = sessions.get(session_id)
        suffix = "/internal/sessions/" + session_id + "/close"
        if not session or not url.endswith(suffix):
            conflicts.append("Unresolved legacy browser dependency " + dependency_id)
            continue
        try:
            identity = add("relay", session["directory"], url[: -len(suffix)], environment)
            dep_changes.append((dependency_id, identity))
        except (KeyError, ValueError, TypeError):
            conflicts.append("Invalid legacy browser dependency " + dependency_id)
    plan = {
        "version": 1,
        "source_sha256": source_hash,
        "connections": [entries[k] for k in sorted(entries)],
        "conflicts": conflicts,
    }
    return plan, settings, private, changes, dep_changes


def migrate(runtime_dir, approved_plan, *, maintenance=False, credentials=None):
    if not maintenance:
        raise ValueError("Stop browser consumers and request maintenance migration")
    plan, settings, private, changes, dep_changes = inventory(runtime_dir)
    if plan["conflicts"]:
        raise ValueError("Resolve the conflicts in the migration plan first")
    reviewed = json.loads(json.dumps(approved_plan))
    for entry in reviewed.get("connections", []):
        if entry.get("approved") is not True:
            raise ValueError("Every legacy connection needs explicit owner approval")
        entry["approved"] = False
    if reviewed != plan:
        raise ValueError("Migration plan or source changed; generate and review a fresh plan")
    credentials = credentials or Credentials(runtime_dir)
    # Resolve all explicitly approved selectors before writing anything.
    tokens = {}
    for entry in plan["connections"]:
        token = os.environ.get(entry["environment"])
        if not token and not entry["required"]:
            continue
        if not token or len(token) > 8192 or "\r" in token or "\n" in token:
            raise ValueError("An approved credential is unavailable; no configuration was migrated")
        tokens[entry["id"]] = token
    refs = {
        e["id"]: "cred_" + _digest(["browser-migration-v1", e["id"]])[:32]
        for e in plan["connections"]
        if e["id"] in tokens
    }
    touched = set()
    for key, data, old, new, identity in changes:
        data.pop(old)
        data[new] = refs.get(identity)
        touched.add(key)
    for _, value in settings.values():
        Settings.model_validate(value)
    for value in private.values():
        if value.get("connection"):
            WorkerConnection.model_validate(value["connection"])
    if not touched and not dep_changes:
        return {"migrated_credentials": 0, "settings_rows": 0, "private_rows": 0, "dependencies": 0}
    runtime = Path(runtime_dir).absolute()
    path = runtime / "addons/browser-sessions-addon/state.db"
    dep_path = runtime / "session-dependencies.db"
    backups = []
    for source in [path, *([dep_path] if dep_path.exists() else [])]:
        backup = source.with_name(source.name + ".credentials-" + uuid4().hex + ".bak")
        descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        with (
            closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original,
            closing(sqlite3.connect(backup)) as copy,
        ):
            original.backup(copy)
        backups.append(str(backup))
    backups.append(str(credentials.store.backup("browser-credential-migration")))
    with closing(sqlite3.connect(path, timeout=10)) as db:
        if dep_path.exists():
            db.execute("ATTACH DATABASE ? AS dependency_store", (str(dep_path),))
        try:
            db.execute("BEGIN IMMEDIATE")
            if inventory(runtime_dir)[0] != plan:
                raise ValueError("Browser state changed; generate and review a fresh plan")
            for entry in plan["connections"]:
                ref = refs.get(entry["id"])
                if ref is None:
                    continue
                try:
                    existing = credentials.store.metadata(ref)
                except CredentialError:
                    existing = None
                if existing is None:
                    credentials.store.create(
                        ref=ref,
                        owner=entry["directory"],
                        kind="resource",
                        label="Browser " + entry["binding"]["purpose"],
                        bindings=[entry["binding"]],
                        values={"token": tokens[entry["id"]]},
                    )
                else:
                    values, _ = credentials.store.resolve(
                        ref, directory=entry["directory"], binding=entry["binding"], kind="resource"
                    )
                    if existing["owner_directory"] != entry["directory"] or values != {
                        "token": tokens[entry["id"]]
                    }:
                        raise ValueError(
                            "Previously imported credential changed; migration stopped"
                        )
                refs[entry["id"]] = ref
            # Validate full replacement settings before any configuration commit.
            for directory, (revision, value) in settings.items():
                Settings.model_validate(value)
                if "settings:" + directory in touched:
                    db.execute(
                        "UPDATE settings SET revision=?,payload=? WHERE directory=?",
                        (revision + 1, json.dumps(value), directory),
                    )
            for session_id, value in private.items():
                if value.get("connection"):
                    WorkerConnection.model_validate(value["connection"])
                db.execute(
                    "INSERT OR REPLACE INTO private_sessions VALUES(?,?)",
                    (session_id, json.dumps(value)),
                )
            if dep_path.exists():
                if "managed_auth" not in {
                    r[1] for r in db.execute("PRAGMA dependency_store.table_info(dependencies)")
                }:
                    db.execute(
                        "ALTER TABLE dependency_store.dependencies ADD COLUMN managed_auth TEXT"
                    )
                entries = {e["id"]: e for e in plan["connections"]}
                for dependency_id, identity in dep_changes:
                    entry = entries[identity]
                    auth = {
                        "credential_ref": refs[identity],
                        "directory": entry["directory"],
                        "binding": entry["binding"],
                    }
                    db.execute(
                        "UPDATE dependency_store.dependencies SET token_env=NULL,managed_auth=? WHERE id=?",
                        (json.dumps(auth), dependency_id),
                    )
            db.commit()
        except Exception:
            db.rollback()
            raise
    return {
        "migrated_credentials": len(refs),
        "settings_rows": sum(k.startswith("settings:") for k in touched),
        "private_rows": len(private),
        "dependencies": len(dep_changes),
        "backups": backups,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument(
        "--plan",
        type=Path,
        help="Reviewed metadata-only plan with approved=true for every connection",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--maintenance", action="store_true")
    args = parser.parse_args()
    try:
        if args.apply:
            if args.plan is None:
                raise ValueError("--apply requires an explicitly reviewed --plan")
            result = migrate(
                args.runtime_dir, json.loads(args.plan.read_text()), maintenance=args.maintenance
            )
        else:
            result = inventory(args.runtime_dir)[0]
        print(json.dumps(result, indent=2))
    except Exception:
        parser.exit(
            1,
            "Browser credential migration failed. Check the plan, source revisions, credential availability and key access; secret values were not printed.\n",
        )


if __name__ == "__main__":
    main()
