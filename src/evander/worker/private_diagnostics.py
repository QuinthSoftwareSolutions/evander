"""Bounded operator-only job evidence, never mounted or returned as artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import shutil
import stat
import threading
import time

from evander.session_artifacts import (
    normalize_artifact_relative_path,
    open_relative_regular_file_no_follow,
)

from .models import validate_worker_component_id

logger = logging.getLogger(__name__)
_RESERVE = 8192
_MAX_EVENT = 512 * 1024
_MAX_FILE = 8 * 1024 * 1024
_EXCLUDED = {"auth.json", "config.toml", "secrets.env", ".env"}


class PrivateJobDiagnostics:
    def __init__(
        self,
        root: Path,
        *,
        active_jobs=lambda: set(),
        retention_days=7,
        max_job_bytes=128 * 1024 * 1024,
        max_total_bytes=1024 * 1024 * 1024,
        clock=time.time,
    ):
        if retention_days <= 0 or max_job_bytes <= _RESERVE or max_total_bytes < max_job_bytes:
            raise ValueError("Invalid private diagnostics retention limits")
        self.root = root
        self.active_jobs = active_jobs
        self.retention_seconds = retention_days * 86400
        self.max_job_bytes = max_job_bytes
        self.max_total_bytes = max_total_bytes
        self.clock = clock
        self.lock = threading.RLock()
        self.captures = {}
        self.sizes = {}
        self.closed = threading.Event()
        self.thread = None
        if root.exists() or root.is_symlink():
            self._directory(root)
            self.prune()
            self._start_maintenance()

    @classmethod
    def from_environment(cls, root, **kwargs):
        return cls(
            root,
            **kwargs,
            retention_days=int(os.environ.get("EVANDER_WORKER_DIAGNOSTICS_RETENTION_DAYS", "7")),
            max_job_bytes=int(os.environ.get("EVANDER_WORKER_DIAGNOSTICS_JOB_MIB", "128"))
            * 1024**2,
            max_total_bytes=int(os.environ.get("EVANDER_WORKER_DIAGNOSTICS_TOTAL_MIB", "1024"))
            * 1024**2,
        )

    def _directory(self, path):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        value = path.lstat()
        if not stat.S_ISDIR(value.st_mode) or value.st_uid != os.geteuid():
            raise ValueError("Private diagnostics directory must be worker-owned")
        path.chmod(0o700)

    def _start_maintenance(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._maintenance, daemon=True)
            self.thread.start()

    def _maintenance(self):
        while not self.closed.wait(300):
            try:
                self.prune()
            except Exception as exc:
                logger.warning(
                    "worker_private_diagnostics_retention_failed exception_class=%s",
                    type(exc).__name__,
                )

    def close(self):
        self.closed.set()
        if self.thread is not None:
            self.thread.join(timeout=2)

    def job_ids(self):
        with self.lock:
            if not self.root.exists():
                return ()
            return tuple(
                path.name for path in self.root.iterdir() if path.is_dir() and not path.is_symlink()
            )

    def prune(self, *, reserve_bytes=0):
        with self.lock:
            if not self.root.exists():
                return
            active = set(self.active_jobs())
            active.update(key for key, value in self.captures.items() if not value.finished)
            directories = []
            for path in self.root.iterdir():
                if path.is_symlink() or not path.is_dir():
                    continue
                directories.append(path)
                if (
                    path.name not in active
                    and self.clock() - path.stat().st_mtime > self.retention_seconds
                ):
                    shutil.rmtree(path)
                    self.captures.pop(path.name, None)
            self.sizes = {
                path: path.stat().st_size
                for path in self.root.rglob("*")
                if path.is_file() and not path.is_symlink()
            }
            for path in sorted(directories, key=lambda p: p.stat().st_mtime if p.exists() else 0):
                if sum(self.sizes.values()) + reserve_bytes <= self.max_total_bytes:
                    break
                if path.name not in active and path.exists():
                    shutil.rmtree(path)
                    self.sizes = {
                        p: size for p, size in self.sizes.items() if not p.is_relative_to(path)
                    }
                    self.captures.pop(path.name, None)

    def begin(self, job_id, *, metadata=None):
        job_id = validate_worker_component_id(job_id, field_name="job_id")
        with self.lock:
            if job_id in self.captures:
                return self.captures[job_id]
            self._directory(self.root)
            self.prune(reserve_bytes=_RESERVE)
            if sum(self.sizes.values()) + _RESERVE > self.max_total_bytes:
                raise OSError("Private diagnostics storage budget exhausted")
            directory = self.root / job_id
            self._directory(directory)
            capture = JobDiagnosticCapture(self, directory, metadata or {})
            existing = directory / "summary.json"
            if existing.exists():
                try:
                    fd = open_relative_regular_file_no_follow(
                        self.root, existing.relative_to(self.root)
                    )
                    with os.fdopen(fd, "rb") as source:
                        previous = json.loads(source.read(_RESERVE))
                    if not isinstance(previous, dict):
                        raise ValueError("Invalid diagnostics summary")
                except (OSError, ValueError) as exc:
                    previous = {}
                    capture.errors.add(type(exc).__name__)
                capture.started_at = previous.get("created_at", capture.started_at)
                capture.dropped = previous.get("dropped_records", 0)
                capture.rotated_bytes = previous.get("rotated_event_bytes", 0)
                capture.finished = previous.get("finished") is True
                capture.errors.update(previous.get("capture_error_types", []))
            self.captures[job_id] = capture
            self._start_maintenance()
            if not capture.finished:
                capture.summary()
            return capture

    def _put(self, capture, relative, data, *, append=False, summary=False):
        path = capture.directory / normalize_artifact_relative_path(relative)
        with self.lock:
            old_size = self.sizes.get(path, 0)
            delta = len(data) if append else len(data) - old_size
            job_size = sum(
                size for p, size in self.sizes.items() if p.is_relative_to(capture.directory)
            )
            reserve = 0 if summary else _RESERVE
            active_reserve = (
                0 if summary else _RESERVE * sum(not c.finished for c in self.captures.values())
            )
            if sum(self.sizes.values()) + delta + active_reserve > self.max_total_bytes:
                self.prune(reserve_bytes=delta + active_reserve)
            if (
                job_size + delta + reserve > self.max_job_bytes
                or sum(self.sizes.values()) + delta + active_reserve > self.max_total_bytes
            ):
                capture.dropped += 1
                return False
            parent = path.parent
            for item in reversed(list(parent.parents)):
                if item.is_relative_to(capture.directory):
                    self._directory(item)
            self._directory(parent)
            temporary = path.with_name(".summary-" + os.urandom(8).hex()) if summary else None
            destination = temporary or path
            flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else 0)
            if temporary:
                flags |= os.O_EXCL
            try:
                fd = open_relative_regular_file_no_follow(
                    self.root, destination.relative_to(self.root), flags=flags, mode=0o600
                )
                with os.fdopen(fd, "wb") as output:
                    if os.fstat(output.fileno()).st_nlink != 1:
                        raise ValueError("Private diagnostics file must not be hard-linked")
                    os.fchmod(output.fileno(), 0o600)
                    if not append:
                        output.truncate(0)
                    try:
                        output.write(data)
                        output.flush()
                        if temporary:
                            os.fsync(output.fileno())
                    finally:
                        if not temporary:
                            self.sizes[path] = os.fstat(output.fileno()).st_size
                if temporary:
                    temporary.replace(path)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
            self.sizes[path] = old_size + len(data) if append else len(data)
            return True


class JobDiagnosticCapture:
    def __init__(self, store, directory, metadata):
        self.store = store
        self.directory = directory
        self.metadata = metadata
        self.dropped = 0
        self.rotated_bytes = 0
        self.errors = set()
        self.finished = False
        self.finalization_lock = threading.Lock()
        self.started_at = datetime.now(UTC).isoformat()

    def summary(self, **fields):
        value = {
            "created_at": self.started_at,
            "finished": self.finished,
            "dropped_records": self.dropped,
            "capture_error_types": sorted(self.errors),
            "rotated_event_bytes": self.rotated_bytes,
            **self.metadata,
            **fields,
        }
        encoded = json.dumps(value, indent=2).encode()
        if len(encoded) > _RESERVE:
            outcome = fields.get("outcome", {})
            value = {
                **self.metadata,
                "created_at": self.started_at,
                "finished": fields.get("finished", self.finished),
                "completed_at": fields.get("completed_at"),
                "dropped_records": self.dropped,
                "rotated_event_bytes": self.rotated_bytes,
                "capture_error_types": sorted(self.errors),
                "summary_truncated": True,
                "outcome": {
                    key: outcome.get(key) for key in ("status", "exit_code", "failure_cause")
                },
            }
            encoded = json.dumps(value).encode()
        self.store._put(self, "summary.json", encoded, summary=True)

    def event(self, event, data):
        try:
            with self.store.lock:
                if self.finished:
                    return
                value = {"time": datetime.now(UTC).isoformat(), "event": event, "data": data}
                encoded = json.dumps(value, ensure_ascii=True).encode()
                if len(encoded) > _MAX_EVENT:
                    self.dropped += 1
                    encoded = json.dumps(
                        {
                            "time": value["time"],
                            "event": event,
                            "truncated": True,
                            "original_bytes": len(encoded),
                            "head": encoded[: _MAX_EVENT // 4].decode("ascii"),
                            "tail": encoded[-_MAX_EVENT // 4 :].decode("ascii"),
                        }
                    ).encode()
                current = self.directory / "events.jsonl"
                if self.store.sizes.get(current, 0) + len(encoded) > self.store.max_job_bytes // 4:
                    previous = self.directory / "events.previous.jsonl"
                    self.rotated_bytes += self.store.sizes.get(previous, 0)
                    previous.unlink(missing_ok=True)
                    self.store.sizes.pop(previous, None)
                    if current.exists():
                        current.replace(previous)
                        self.store.sizes[previous] = self.store.sizes.pop(current, 0)
                self.store._put(self, "events.jsonl", encoded + b"\n", append=True)
        except Exception as exc:
            self.errors.add(type(exc).__name__)

    def collect(self, artifact_root, source_paths):
        count = 0
        started = time.monotonic()
        for source in source_paths[:32]:
            try:
                relative = normalize_artifact_relative_path(source)
                start = artifact_root / relative
                if start.is_symlink():
                    raise ValueError("Diagnostics source must not be a symlink")
                candidates = start.rglob("*") if start.is_dir() else [start]
                for path in candidates:
                    count += 1
                    if count > 1024 or time.monotonic() - started > 5:
                        self.dropped += 1
                        return
                    try:
                        self._collect_file(artifact_root, path)
                    except (OSError, ValueError, TypeError) as exc:
                        self.errors.add(type(exc).__name__)
            except (OSError, ValueError, TypeError) as exc:
                self.errors.add(type(exc).__name__)

    def _collect_file(self, artifact_root, path):
        if path.is_symlink() or not path.is_file() or path.name in _EXCLUDED:
            return
        source_relative = path.relative_to(artifact_root)
        fd = open_relative_regular_file_no_follow(artifact_root, source_relative, missing_ok=True)
        if fd is None:
            return
        with os.fdopen(fd, "rb") as stream:
            if os.fstat(stream.fileno()).st_nlink != 1:
                raise ValueError("Diagnostics source must not be hard-linked")
            size = os.fstat(stream.fileno()).st_size
            if size > _MAX_FILE:
                stream.seek(size - _MAX_FILE)
                self.dropped += 1
            data = stream.read(_MAX_FILE)
        self.store._put(self, "files/" + source_relative.as_posix(), data)

    def finish(self, **outcome):
        with self.store.lock:
            if self.finished:
                return
            self.event("worker.completed", outcome)
            self.summary(finished=True, completed_at=datetime.now(UTC).isoformat(), outcome=outcome)
            self.finished = True
            os.utime(self.directory, None)
