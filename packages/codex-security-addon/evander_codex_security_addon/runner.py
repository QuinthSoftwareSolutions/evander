#!/usr/bin/env python3
"""Session-local runner. Only this process reads repository contents."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from uuid import uuid4

INSTALL_ROOT = Path(__file__).resolve().parent
HOME = Path("/home/agent")
SCAN_ROOT = Path("/artifacts/security-scans")


def scan_directory(scan_id):
    if not re.fullmatch(r"[a-f0-9]{32}", scan_id):
        raise ValueError("Invalid scan identity")
    if SCAN_ROOT.is_symlink():
        raise ValueError("Scan storage must not be a symbolic link")
    SCAN_ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
    root = SCAN_ROOT / scan_id
    if root.is_symlink():
        raise ValueError("Scan directory must not be a symbolic link")
    root.mkdir(mode=0o700, exist_ok=True)
    if not root.resolve().is_relative_to(SCAN_ROOT.resolve()):
        raise ValueError("Scan directory escapes artifact storage")
    return root


def write_json(path, value):
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)


def read_json(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Invalid scan artifact")
    return json.loads(path.read_text())


@contextmanager
def state_lock(root):
    # This lock covers process publication and terminal state only. The lifetime
    # runner lock must never prevent cancellation of a running scan.
    with (root / "state.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def git_environment():
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    return env


def load_session_environment():
    # Container jobs execute this trusted program directly, without a login shell.
    # Read the host-provisioned session token as data; never execute a shell profile.
    for path in (HOME / ".evander/session.env", Path("/etc/evander/session.env")):
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key == "EVANDER_MCP_SESSION_TOKEN":
                values = shlex.split(value)
                if len(values) == 1:
                    os.environ.setdefault(key, values[0])
    os.environ.setdefault(
        "OPENAI_API_KEY", os.environ.get("EVANDER_MCP_SESSION_TOKEN") or "relay-placeholder"
    )


def git(repo, *args):
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(repo),
            *args,
        ],
        env=git_environment(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode:
        raise ValueError("Git snapshot operation failed: " + result.stderr.strip()[:1000])
    return result.stdout.strip()


def snapshot(request, root):
    original = Path(request["repo_path"])
    repo = original.resolve(strict=True)
    if not repo.is_relative_to(HOME.resolve()) or original.is_symlink():
        raise ValueError("Repository must be contained in the session workspace")
    if Path(git(repo, "rev-parse", "--show-toplevel")).resolve() != repo:
        raise ValueError("repo_path must name the Git worktree root")
    for key in ("from_commit", "to_commit"):
        if request.get(key) and not re.fullmatch(r"[0-9a-fA-F]{7,64}", request[key]):
            raise ValueError("Invalid commit hash")
    head = git(
        repo,
        "rev-parse",
        "--verify",
        "--end-of-options",
        (request.get("to_commit") or "HEAD") + "^{commit}",
    )
    base = None
    if request.get("from_commit"):
        base = git(
            repo, "rev-parse", "--verify", "--end-of-options", request["from_commit"] + "^{commit}"
        )
    elif request.get("to_commit"):
        parents = git(repo, "rev-list", "--parents", "-n", "1", head).split()
        base = parents[1] if len(parents) > 1 else None
    target = root / "checkout"
    if target.exists():
        shutil.rmtree(target)
    # No shared object store, hooks, checkout, credentials, or working files from the source.
    subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "clone",
            "--no-local",
            "--no-checkout",
            "--",
            str(repo),
            str(target),
        ],
        check=True,
        capture_output=True,
        timeout=120,
        env=git_environment(),
    )
    # Explicitly fetch pinned objects, including commits not reachable from a branch.
    git(
        target,
        "fetch",
        "--no-tags",
        "--",
        str(repo),
        *dict.fromkeys([head, *([base] if base else [])]),
    )
    git(target, "checkout", "--detach", head)
    git(target, "remote", "remove", "origin")
    if base:
        git(target, "cat-file", "-e", base + "^{commit}")
    return target, base, head


def canonical_result(root, plugin_root, request):
    candidates = []
    for path in (root / "reports").rglob("scan-manifest.json"):
        manifest = read_json(path)
        scan = manifest.get("scan", {})
        if scan.get("status") != "completed" or not scan.get("sealedAt"):
            continue
        coverage = read_json(path.parent / "coverage.json")
        if (coverage.get("mode") == "deep_repository") != (request["mode"] == "deep"):
            continue
        candidates.append(path.parent)
    if len(candidates) != 1:
        raise ValueError("Scan did not produce exactly one sealed result for the requested mode")
    directory = candidates[0]
    script = plugin_root / "scripts/validate_scan_contract.py"
    spec = importlib.util.spec_from_file_location("evander_codex_scan_validation", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.validate_contract(directory)
    native = result["manifest"]["scan"]
    target = native["target"]
    coordinates = read_json(root / "target.json")
    if coordinates["from_commit"]:
        if target.get("kind") != "git_diff":
            raise ValueError("Expected a committed diff scan")
        if result["coverage"]["mode"] not in {"diff", "commit", "branch_diff"}:
            raise ValueError("Expected committed diff coverage")
        for key, field in (("baseRevision", "from_commit"), ("headRevision", "to_commit")):
            if target.get(key) != coordinates[field]:
                raise ValueError("Sealed scan range does not match the requested commits")
    else:
        if (
            target.get("kind") not in {"git_revision", "git_worktree"}
            or target.get("revision") != coordinates["to_commit"]
        ):
            raise ValueError("Sealed scan revision does not match the requested snapshot")
        if native["scope"].get("includePaths") != ["."]:
            raise ValueError("Sealed scan does not cover the whole repository")
        expected_mode = "deep_repository" if request["mode"] == "deep" else "repository"
        if result["coverage"]["mode"] != expected_mode:
            raise ValueError("Unexpected scan coverage mode")
    findings = []
    counts = {key: 0 for key in ("critical", "high", "medium", "low", "informational")}
    for finding in result["findings"]["findings"]:
        severity = finding["severity"]["level"]
        counts[severity] += 1
        findings.append(
            {
                "id": finding["findingId"],
                "occurrence_id": finding["occurrenceId"],
                "title": finding["title"],
                "severity": severity,
                "locations": finding.get("locations", []),
            }
        )
    coverage = result["coverage"]["completeness"]
    return {
        "status": "completed" if coverage == "complete" else "failed",
        "provider_scan_id": native["id"],
        **coordinates,
        "coverage": coverage,
        "findings": findings,
        "severity_counts": counts,
        "artifacts": {
            name: str((directory / name).relative_to(SCAN_ROOT.parent))
            for name in ("report.md", "scan-manifest.json", "findings.json", "coverage.json")
        },
        "error": None if coverage == "complete" else "Security scan coverage is incomplete",
    }


def run(request):
    if os.getpgrp() != os.getpid():
        os.setsid()
    root = scan_directory(request["scan_id"])
    with (root / "runner.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        with state_lock(root):
            if (root / "result.json").exists():
                return
            write_json(
                root / "process.json", {"pid": os.getpid(), "start": process_start(os.getpid())}
            )
            write_json(root / "request.json", request)
        child = None
        result = {"status": "failed", "error": "Scan ended before producing a result"}
        try:
            load_session_environment()
            if request["mode"] not in {"standard", "deep"}:
                raise ValueError("Invalid scan mode")
            if request["mode"] == "deep" and (
                request.get("from_commit") or request.get("to_commit")
            ):
                raise ValueError("Deep scans do not support commit bounds")
            target, base, head = snapshot(request, root)
            coordinates = {"from_commit": base, "to_commit": head}
            write_json(root / "target.json", coordinates)
            if base and not git(target, "diff", "--name-only", base, head):
                result = {
                    "status": "completed",
                    **coordinates,
                    "coverage": "complete",
                    "findings": [],
                    "severity_counts": {},
                    "artifacts": {},
                }
            else:
                setup = subprocess.run(
                    [str(INSTALL_ROOT / "node"), str(INSTALL_ROOT / "configure.mjs"), str(root)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                config = json.loads(setup.stdout)
                plugin = Path(config["plugin_root"])
                skill = (
                    "deep-security-scan"
                    if request["mode"] == "deep"
                    else "security-diff-scan"
                    if base
                    else "security-scan"
                )
                prompt = (
                    f"Run the installed Codex Security {skill} skill to completion. "
                    f"Repository: {target}. Scope: the entire repository. "
                    + (
                        f"Review exactly the committed range {base} to {head}. "
                        if base
                        else f"Pinned revision: {head}. "
                    )
                    + "This is unattended automation. Do not ask for input. Start exactly one scan, "
                    "perform every required phase, and complete that same scan once. "
                    "Use the configured scan root. Do not fix code, publish issues, or make pull requests. "
                    "A conversation response alone is not a scan result. Preserve sealed canonical artifacts."
                )
                env = dict(os.environ)
                env.update(config["environment"])
                command = [
                    sys.executable,
                    str(INSTALL_ROOT / "codex_bridge.py"),
                    "exec",
                    "--json",
                    "--skip-git-repo-check",
                    "--cd",
                    str(root),
                    "--config",
                    'default_permissions="codex_security_scan"',
                    "--config",
                    'approval_policy="never"',
                    prompt,
                ]
                with (root / "scan.log").open("w") as log:
                    with state_lock(root):
                        if (root / "result.json").exists():
                            return
                        child = subprocess.Popen(
                            command,
                            env=env,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                        )
                        try:
                            write_json(
                                root / "process.json",
                                {
                                    "pid": os.getpid(),
                                    "start": process_start(os.getpid()),
                                    "child_pid": child.pid,
                                    "child_start": process_start(child.pid),
                                },
                            )
                        except BaseException:
                            os.killpg(child.pid, signal.SIGKILL)
                            child.wait()
                            raise
                    try:
                        code = child.wait(timeout=float(request["timeout_seconds"]))
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGTERM)
                        try:
                            child.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            os.killpg(child.pid, signal.SIGKILL)
                        child.wait()
                        result = {
                            "status": "timed_out",
                            **coordinates,
                            "error": "Security scan timed out",
                        }
                    else:
                        if git(target, "status", "--porcelain=v1", "--untracked-files=all"):
                            raise ValueError("Scanner modified the pinned repository snapshot")
                        result = canonical_result(root, plugin, request)
                        if code and result["status"] == "completed":
                            result.update(status="failed", error=f"Scanner exited with code {code}")
        except Exception as exc:
            result = {
                "status": "failed",
                "error": str(exc)[:2000],
                "artifacts": {"diagnostics": str(root.relative_to(SCAN_ROOT.parent))},
            }
        finally:
            if child and child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            shutil.rmtree(root / "checkout", ignore_errors=True)
            shutil.rmtree(root / "codex-home", ignore_errors=True)
            with state_lock(root):
                if not (root / "result.json").exists():
                    write_json(root / "result.json", result)


def process_start(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def status(scan_id, *, check_process=True):
    root = scan_directory(scan_id)
    if (root / "result.json").exists():
        return read_json(root / "result.json")
    if check_process and (root / "process.json").exists():
        process = read_json(root / "process.json")
        if process_start(process["pid"]) != process["start"]:
            return {
                "status": "failed",
                "error": "Scan process was interrupted",
                "artifacts": {"diagnostics": str(root.relative_to(SCAN_ROOT.parent))},
            }
    return {"status": "running"}


def terminate_group(pid, started):
    if not started or process_start(pid) != started:
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 2
    while process_start(pid) == started and time.monotonic() < deadline:
        time.sleep(0.05)
    # The group can outlive its leader. Avoid a reused PID, but stop descendants
    # that ignored TERM even when their original parent has already exited.
    if process_start(pid) in {None, started}:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def cancel(scan_id):
    root = scan_directory(scan_id)
    with state_lock(root):
        if (root / "result.json").exists():
            if read_json(root / "result.json")["status"] != "cancelled":
                return status(scan_id)
        else:
            write_json(
                root / "result.json",
                {
                    "status": "cancelled",
                    "artifacts": {"diagnostics": str(root.relative_to(SCAN_ROOT.parent))},
                },
            )
        process_path = root / "process.json"
        if not process_path.exists():
            process_path = root / "launched.json"
        process = read_json(process_path) if process_path.exists() else None
    # Publish cancellation before terminating anything: a runner starting or
    # finalizing concurrently will now leave this terminal receipt untouched.
    if process:
        if process.get("child_pid"):
            terminate_group(process["child_pid"], process.get("child_start"))
        terminate_group(process["pid"], process.get("start"))
    return status(scan_id)


def launch(request):
    root = scan_directory(request["scan_id"])
    with state_lock(root):
        if not (root / "result.json").exists() and not (root / "launched.json").exists():
            with (root / "launcher.log").open("a") as log:
                child = subprocess.Popen(
                    [sys.executable, __file__, "run", json.dumps(request)],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            try:
                write_json(
                    root / "launched.json", {"pid": child.pid, "start": process_start(child.pid)}
                )
            except BaseException:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
                raise
    return status(request["scan_id"], check_process=False)


def main():
    action, value = sys.argv[1:3]
    if action in {"run", "launch"}:
        request = json.loads(value)
        if action == "launch":
            print(json.dumps(launch(request)))
        else:
            run(request)
    elif action in {"status", "status-live"}:
        # A helper container has a different PID namespace from the scan job.
        print(json.dumps(status(value, check_process=action == "status-live")))
    elif action == "files":
        root = scan_directory(value)
        names = ("reports", "result.json", "target.json", "scan.log", "launcher.log")
        print(
            json.dumps(
                {
                    "paths": [
                        str((root / name).relative_to(SCAN_ROOT.parent))
                        for name in names
                        if (root / name).exists()
                    ]
                }
            )
        )
    elif action == "cancel":
        print(json.dumps(cancel(value)))
    else:
        raise ValueError("Unknown runner operation")


if __name__ == "__main__":
    main()
