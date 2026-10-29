"""Optional controller-side timings; never record task arguments or result payloads."""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

from ansible.plugins.callback import CallbackBase


DOCUMENTATION = r"""
name: deployment_timings
type: aggregate
short_description: Report deployment task and role durations
description:
  - Enable with ANSIBLE_CALLBACKS_ENABLED=deployment_timings.
  - Optionally write JSON using EVANDER_DEPLOYMENT_TIMINGS_FILE on the controller.
  - Arguments, results, host facts, and names of no_log tasks are excluded.
requirements:
  - Enable this plugin in callbacks_enabled and add its directory to callback_plugins.
"""


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "deployment_timings"
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._started = time.monotonic()
        self._tasks = []
        self._active = {}

    def v2_playbook_on_start(self, playbook):
        self._started = time.monotonic()

    def _start_task(self, task):
        record = {
            "name": "(redacted)" if task.no_log else task.get_name().strip(),
            "role": task._role.get_name() if task._role else "(playbook)",
            "elapsed_seconds": 0.0,
            "outcomes": Counter(),
            "started": time.monotonic(),
        }
        self._tasks.append(record)
        self._active[str(task._uuid)] = record

    def v2_playbook_on_task_start(self, task, is_conditional):
        self._start_task(task)

    def v2_playbook_on_handler_task_start(self, task):
        self._start_task(task)

    def _finish_task(self, result, outcome):
        record = self._active.get(str(result._task._uuid))
        if record is None:
            self._start_task(result._task)
            record = self._active[str(result._task._uuid)]
        if result._task.no_log or result._result.get("_ansible_no_log"):
            record["name"] = "(redacted)"
        record["elapsed_seconds"] = time.monotonic() - record["started"]
        record["outcomes"][outcome] += 1

    def v2_runner_on_ok(self, result):
        self._finish_task(result, "ok")

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._finish_task(result, "failed")

    def v2_runner_on_skipped(self, result):
        self._finish_task(result, "skipped")

    def v2_runner_on_unreachable(self, result):
        self._finish_task(result, "unreachable")

    def v2_playbook_on_stats(self, stats):
        tasks = [
            {
                "name": record["name"],
                "role": record["role"],
                "elapsed_seconds": round(record["elapsed_seconds"], 6),
                "outcomes": dict(record["outcomes"]),
            }
            for record in self._tasks
            if record["outcomes"]
        ]
        role_seconds = defaultdict(float)
        for task in tasks:
            role_seconds[task["role"]] += task["elapsed_seconds"]
        report = {
            "schema_version": 1,
            "elapsed_seconds": round(time.monotonic() - self._started, 6),
            "roles": dict(sorted(role_seconds.items())),
            "tasks": tasks,
        }
        self._display.banner("Deployment timings (seconds)")
        self._display.display(f"Playbook: {report['elapsed_seconds']:.3f}")
        for role, elapsed in sorted(role_seconds.items(), key=lambda item: -item[1]):
            self._display.display(f"Role {role}: {elapsed:.3f}")
        for task in sorted(tasks, key=lambda item: -item["elapsed_seconds"])[:10]:
            self._display.display(f"Task {task['name']}: {task['elapsed_seconds']:.3f}")

        output = os.environ.get("EVANDER_DEPLOYMENT_TIMINGS_FILE")
        if not output:
            return
        temporary = None
        try:
            path = Path(output)
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
                json.dump(report, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temporary, path)
        except OSError as exc:
            # Instrumentation must not change the outcome of deployment tasks.
            self._display.warning(
                f"Deployment timing report could not be written ({type(exc).__name__})."
            )
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
