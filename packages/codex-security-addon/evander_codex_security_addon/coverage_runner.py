"""Trusted session-side inventory and one isolated SDK review. No scheduling state."""

import fcntl
import codecs
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

if __package__:
    from . import runner
    from .coverage_projection import git_bytes, symlink_projection
else:
    import runner
    from coverage_projection import git_bytes, symlink_projection


def inventory(request):
    repo = Path(request["repo_path"]).resolve(strict=True)
    if not repo.is_relative_to(runner.HOME.resolve()):
        raise ValueError("Repository is outside the session workspace")
    if Path(runner.git(repo, "rev-parse", "--show-toplevel")).resolve() != repo:
        raise ValueError("Repository path is not a worktree root")
    if runner.git(repo, "status", "--porcelain=v1", "--untracked-files=no"):
        raise ValueError("Tracked coverage requires a clean checkout")
    revision = runner.git(repo, "rev-parse", "HEAD^{commit}")
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(repo),
            "ls-tree",
            "-r",
            "-z",
            "-l",
            "--full-tree",
            revision,
        ],
        capture_output=True,
        check=True,
        timeout=120,
        env=runner.git_environment(),
    )
    entries = []
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        metadata, path = record.split(b"\t", 1)
        mode, _, oid, size = metadata.split()
        name = path.decode("utf-8", "surrogateescape")
        kind = {b"120000": "symlink", b"160000": "gitlink"}.get(mode, "text")
        if kind == "text":
            try:
                fd = os.open(repo / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "rb") as source:
                    decoder = codecs.getincrementaldecoder("utf-8")()
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        if b"\0" in chunk:
                            kind = "binary"
                            break
                        decoder.decode(chunk, final=False)
                    if kind == "text":
                        decoder.decode(b"", final=True)
            except UnicodeError:
                kind = "binary"
            except OSError:
                kind = "unavailable"
        entries.append(
            {
                "path": name,
                "mode": mode.decode(),
                "oid": oid.decode(),
                "size": 0 if size == b"-" else int(size),
                "kind": kind,
            }
        )
    return {"coverage_runtime": 1, "revision": revision, "entries": entries}


def review_projection(target, request, *, materialize=False):
    work = request["coverage_task"]
    if work.get("kind") == "architecture":
        return None
    if work.get("assigned_entries") and not any(
        x["mode"] == "120000" for x in work["assigned_entries"]
    ):
        return None
    return symlink_projection(
        target, request["snapshot_commit"], work["paths"], materialize=materialize
    )


def prepare_review(root, request):
    target = root / "checkout"
    projection = review_projection(target, request, materialize=True)
    if projection:
        git_bytes(target, "checkout", "--detach", projection["revision"])
    runner.write_json(root / "review-target.json", {"source_projection": projection})
    return projection


def validate_snapshot(root, request):
    target = root / "checkout"
    projection = review_projection(target, request)
    revision = projection["revision"] if projection else request["snapshot_commit"]
    if runner.git(target, "rev-parse", "HEAD^{commit}") != revision or runner.git(
        target, "status", "--porcelain=v1", "--untracked-files=all"
    ):
        raise ValueError("Scoped reviewer changed the pinned checkout")
    runner.write_json(root / "snapshot-validation.json", {"revision": request["snapshot_commit"]})


def recover(scan_id):
    """Called only after the worker is observed stopped; never launch a model."""
    root = runner.scan_directory(scan_id)
    with runner.state_lock(root):
        if (root / "result.json").exists():
            return runner.read_json(root / "result.json")
        try:
            request = runner.read_json(root / "request.json")
            result = runner.read_json(root / "sdk-result.json")
            if request.get("scan_id") != scan_id or not result.get("coverage_unit"):
                return {"status": "failed", "coverage": "partial"}
            validation = root / "snapshot-validation.json"
            if validation.exists():
                if runner.read_json(validation).get("revision") != request["snapshot_commit"]:
                    raise ValueError("Saved snapshot validation mismatch")
            else:
                validate_snapshot(root, request)
            result["recovered_from_saved_sdk_result"] = True
            runner.write_json(root / "result.json", result)
            return result
        except (ValueError, KeyError, OSError):
            return {
                "status": "failed",
                "coverage": "partial",
                "error": "Interrupted review has no recoverable validated result",
            }


def run_task(request):
    root = runner.scan_directory(request["scan_id"])
    with (root / "runner.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if (root / "result.json").exists():
            return
        runner.write_json(root / "request.json", request)
        runner.write_json(
            root / "process.json", {"pid": os.getpid(), "start": runner.process_start(os.getpid())}
        )
        execution = {"started_at": time.time(), "timeout_seconds": request["timeout_seconds"]}
        result = {
            "status": "failed",
            "coverage": "partial",
            "artifacts": {},
            "error": "Scoped review did not produce a result",
        }
        child = None
        try:
            runner.load_session_environment()
            target, base, revision = runner.snapshot(request, root)
            if base or revision != request["snapshot_commit"]:
                raise ValueError("Scoped review snapshot mismatch")
            runner.write_json(root / "target.json", {"from_commit": None, "to_commit": revision})
            prepare_review(root, request)
            config = subprocess.run(
                [
                    str(runner.INSTALL_ROOT / "node"),
                    str(runner.INSTALL_ROOT / "configure.mjs"),
                    str(root),
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=120,
            )
            setup = json.loads(config.stdout)
            runner.write_json(root / "sdk-setup.json", setup)
            env = {**os.environ, **setup["environment"], "TMPDIR": str(root / "tmp")}
            (root / "tmp").mkdir(mode=0o700, exist_ok=True)
            with (root / "scan.log").open("w") as log:
                child = subprocess.Popen(
                    [
                        str(runner.INSTALL_ROOT / "node"),
                        str(runner.INSTALL_ROOT / "coverage_sdk.mjs"),
                        str(root),
                    ],
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                runner.write_json(
                    root / "process.json",
                    {
                        "pid": os.getpid(),
                        "start": runner.process_start(os.getpid()),
                        "child_pid": child.pid,
                        "child_start": runner.process_start(child.pid),
                    },
                )
                try:
                    execution["child_exit_code"] = child.wait(
                        timeout=float(request["timeout_seconds"])
                    )
                    execution["termination_reason"] = "native_exit"
                except subprocess.TimeoutExpired:
                    execution["termination_reason"] = "timeout"
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=10)
            if (root / "sdk-result.json").exists():
                result = runner.read_json(root / "sdk-result.json")
            validate_snapshot(root, request)
        except Exception as exc:
            execution["exception_class"] = type(exc).__name__
            # A sealed SDK receipt cannot earn credit after snapshot integrity fails.
            result = {
                "status": "failed",
                "coverage": "partial",
                "artifacts": {},
                "error": "Scoped review failed; inspect private diagnostics",
            }
        finally:
            if child and child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=10)
            try:
                execution.update(
                    finished_at=time.time(),
                    native_history=runner.capture_native_diagnostics(
                        root, home=root / "state/codex-home"
                    ),
                )
                runner.write_json(root / "execution.json", execution)
            except Exception:
                pass
            shutil.rmtree(root / "checkout", ignore_errors=True)
            shutil.rmtree(root / "codex-home", ignore_errors=True)
            # SDK account homes contain only placeholders in subscription mode,
            # but are still private and never part of the report operation.
            shutil.rmtree(root / "state/codex-home", ignore_errors=True)
            with runner.state_lock(root):
                if not (root / "result.json").exists():
                    runner.write_json(root / "result.json", result)
