"""Deterministic read-work instrumentation shared by regression tests and probes."""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from unittest.mock import patch


class ReadProbe:
    def __init__(self):
        self.rows = Counter()
        self.payload_rows = Counter()
        self.selects = Counter()
        self.vm_steps = 0
        self.statements = []

    def __enter__(self):
        original_connect = sqlite3.connect
        probe = self

        class Cursor(sqlite3.Cursor):
            table = None
            has_payload = False

            def execute(self, sql, parameters=()):
                normalized = " ".join(sql.lower().split())
                self.table = None
                self.has_payload = "payload_json" in normalized
                if normalized.startswith(("select", "with")):
                    for table in (
                        "worker_jobs",
                        "wake_dispatches",
                        "wake_requests",
                        "teams_inbox_messages",
                        "model_usage_correlation_contexts",
                    ):
                        if f"from {table}" in normalized:
                            self.table = table
                            probe.selects[table] += 1
                            break
                    probe.statements.append(normalized)
                return super().execute(sql, parameters)

            def counted(self, count):
                if self.table:
                    probe.rows[self.table] += count
                    if self.has_payload:
                        probe.payload_rows[self.table] += count

            def fetchone(self):
                row = super().fetchone()
                self.counted(int(row is not None))
                return row

            def fetchmany(self, size=None):
                rows = super().fetchmany() if size is None else super().fetchmany(size)
                self.counted(len(rows))
                return rows

            def fetchall(self):
                rows = super().fetchall()
                self.counted(len(rows))
                return rows

            def __next__(self):
                row = super().__next__()
                self.counted(1)
                return row

        class Connection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                return self.cursor(factory=Cursor).execute(sql, parameters)

        def connect(*args, **kwargs):
            kwargs["factory"] = Connection
            connection = original_connect(*args, **kwargs)

            def progress():
                probe.vm_steps += 1
                return 0

            connection.set_progress_handler(progress, 1)
            return connection

        self._patch = patch.object(sqlite3, "connect", connect)
        self._patch.start()
        return self

    def __exit__(self, *args):
        self._patch.stop()

    def report(self):
        return {
            "rows": dict(self.rows),
            "payload_rows": dict(self.payload_rows),
            "selects": dict(self.selects),
            "sqlite_vm_steps": self.vm_steps,
        }


class EdgeReads(dict):
    def __init__(self, *args, counter, **kwargs):
        super().__init__(*args, **kwargs)
        self.counter = counter

    def get(self, key, default=None):
        self.counter[key] += 1
        return super().get(key, default)


class LineProbe:
    def __init__(self, function_name):
        self.function_name = function_name
        self.line_events = 0

    def __enter__(self):
        self.previous = sys.gettrace()

        def trace(frame, event, arg):
            if frame.f_code.co_name == self.function_name and frame.f_code.co_filename.endswith(
                "/workflow/validation.py"
            ):
                if event == "line":
                    self.line_events += 1
                return trace
            return None

        sys.settrace(trace)
        return self

    def __exit__(self, *args):
        sys.settrace(self.previous)
