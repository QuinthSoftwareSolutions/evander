"""Read-only migration preview. Never edits configuration, moves leases or provisions resources."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .models import Settings, WorkerSettings
import sqlite3


def service_origin(origin, port):
    value = urlsplit(origin)
    hostname = value.hostname
    if ":" in hostname:
        hostname = "[" + hostname + "]"
    return urlunsplit((value.scheme, hostname + ":" + str(port), "", "", ""))


def preview(raw, worker_configs):
    settings = Settings.model_validate(raw.get("settings", raw))
    result = {
        name: {
            "allocations_enabled": False,
            "connections": {},
            "profiles": {},
            "workers": {},
            "max_per_worker": settings.max_per_worker,
            "workflow_operations": [],
        }
        for name in ("sqlserver-addon", "rabbitmq-addon")
    }
    worker_result = {}
    for worker_id, worker in settings.workers.items():
        if worker_id not in worker_configs:
            raise ValueError(
                "Every configured worker needs its nonsecret legacy worker configuration"
            )
        old = WorkerSettings.model_validate(worker_configs[worker_id])
        if old.worker_id != worker_id:
            raise ValueError("Legacy worker identity does not match the control-plane binding")
        worker_result[worker_id] = {}
        for addon_id, port in (("sqlserver-addon", 11992), ("rabbitmq-addon", 11993)):
            prefix = addon_id.removesuffix("-addon")
            destination = result[addon_id]
            destination["workers"][worker_id] = {
                "worker_base_url": worker.worker_base_url,
                "service_url": service_origin(worker.fixture_base_url, port),
                "token_env": "EVANDER_" + prefix.upper() + "_WORKER_TOKEN",
            }
            worker_result[worker_id][addon_id] = {
                "runtime_dir": "/var/lib/evander-" + addon_id,
                "worker_id": worker_id,
                "worker_jobs_db": old.worker_jobs_db,
                "allocations_enabled": False,
                "max_per_worker": settings.max_per_worker,
                "connections": {},
                "allowed_images": [],
                "token_env": destination["workers"][worker_id]["token_env"],
            }
            profile = {
                "worker_id": worker_id,
                "idle_seconds": settings.idle_seconds,
                "allowed_owner_providers": ["team-addon"] if settings.allowed_team_ids else [],
                "allowed_owner_key_prefixes": settings.allowed_team_ids,
                "options": {},
            }
            if prefix == "sqlserver":
                if not old.sql_enabled:
                    continue
                connection_id = "worker-" + worker_id
                connection = {
                    "host": old.sql_host,
                    "application_host": old.sql_container_host,
                    "port": old.sql_port,
                    "encrypt": False,
                    "operations": [],
                    "credentials": {
                        "admin": {
                            "username": old.sql_admin_user,
                            "password_env": old.sql_password_env,
                        }
                    },
                }
                destination["connections"][connection_id] = connection
                worker_result[worker_id][addon_id]["connections"][connection_id] = connection
                profile.update(
                    connection_id=connection_id, options={"databases": ["source", "target"]}
                )
            else:
                if old.rabbitmq_image is None:
                    continue
                profile["options"] = {"mode": "isolated", "image": old.rabbitmq_image}
                worker_result[worker_id][addon_id]["allowed_images"] = [old.rabbitmq_image]
            destination["profiles"]["legacy-" + worker_id] = profile
    tool_map = {
        "test_fixtures_" + action: ["sqlserver_" + action, "rabbitmq_" + action]
        for action in ("acquire", "get", "reset", "release")
    }
    tool_map["test_fixtures_control"] = ["rabbitmq_control"]
    return {
        "settings": result,
        "workers": worker_result,
        "tool_grant_translation": tool_map,
        "notes": [
            "Preview only. Confirm new worker API origins and install separate authentication tokens before saving.",
            "Add discovery grants with the selected lifecycle grants; data and management grants remain empty.",
            "SQL test transport preserves legacy local-worker encryption behavior. Configure trusted TLS explicitly for production.",
            "Do not transfer active leases. Enable legacy drain_only and retain cleanup until retirement checks pass.",
        ],
    }


def retirement_check(control_plane_root, worker_roots):
    paths = {
        "control-plane": Path(control_plane_root),
        **{key: Path(path) for key, path in worker_roots.items()},
    }
    if any(not (root / "leases.db").is_file() for root in paths.values()):
        return {
            "journals_drained": False,
            "reason": "Every supplied journal must exist",
            "retirement_authorized": False,
        }
    blocked, required_workers = [], set()
    for worker_id, root in paths.items():
        with sqlite3.connect((root / "leases.db").resolve().as_uri() + "?mode=ro", uri=True) as db:
            if worker_id == "control-plane":
                for row in db.execute("SELECT payload FROM settings"):
                    required_workers.update(json.loads(row[0]).get("workers", {}))
            for row in db.execute("SELECT payload FROM leases"):
                lease = json.loads(row[0])
                if worker_id == "control-plane":
                    required_workers.add(lease["scope"]["worker_id"])
                elif lease["scope"]["worker_id"] != worker_id:
                    blocked.append({"worker_id": worker_id, "reason": "Journal identity mismatch"})
                if (
                    lease["state"] not in {"released", "expired"}
                    or lease.get("bound_job_id")
                    or lease.get("config_path")
                    or any(lease.get("resource_counts", {}).values())
                    or lease.get("operation")
                    or lease.get("fault_until")
                ):
                    blocked.append({"lease_id": lease["lease_id"], "state": lease["state"]})
    missing = sorted(required_workers - worker_roots.keys())
    return {
        "journals_drained": not blocked and not missing,
        "blocked": blocked,
        "missing_workers": missing,
        "workers_to_verify": sorted(required_workers),
        "retirement_authorized": False,
        "disposition": "Journal checks do not prove provider absence. Independently verify actual resources and bindings before operator retirement.",
    }


def _run():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("preview")
    p.add_argument("--settings", type=Path, required=True)
    p.add_argument("--worker", action="append", default=[], metavar="ID=PATH")
    p = sub.add_parser("retirement-check")
    p.add_argument("--control-plane", type=Path, required=True)
    p.add_argument("--worker", action="append", required=True, metavar="ID=JOURNAL_ROOT")
    args = parser.parse_args()
    if args.command == "preview":
        workers = {
            key: json.loads(Path(path).read_text())
            for key, path in (v.split("=", 1) for v in args.worker)
        }
        result = preview(json.loads(args.settings.read_text()), workers)
    else:
        result = retirement_check(
            args.control_plane, dict(value.split("=", 1) for value in args.worker)
        )
    print(json.dumps(result, indent=2))


def main():
    try:
        _run()
    except (OSError, ValueError, KeyError, sqlite3.Error):
        print(
            json.dumps(
                {
                    "error": "Invalid or unavailable legacy configuration/journal; no state was changed"
                }
            )
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
