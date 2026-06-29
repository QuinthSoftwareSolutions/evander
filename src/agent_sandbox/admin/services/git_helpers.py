from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from agent_sandbox.admin.services import process_helpers
from agent_sandbox.pr_feedback.store import PrFeedbackStore

PROTECTED_MIRROR_HEAD_REFSPECS = ("^refs/heads/agents/*", "^refs/heads/review/*")


def resolve_git_root(root_dir: Path) -> Path:
    env_value = os.environ.get("AGENT_SANDBOX_GIT_ROOT", "").strip()
    if env_value:
        return Path(env_value)
    return root_dir / "git-remotes"


def default_base_branch_for_repo(repo_path: str) -> str | None:
    return default_base_branch_for_repo_remote(repo_path, "origin")


def default_base_branch_for_repo_remote(repo_path: str, remote_name: str) -> str | None:
    candidates = [
        [
            "git",
            "-C",
            repo_path,
            "symbolic-ref",
            "--quiet",
            "--short",
            f"refs/remotes/{remote_name}/HEAD",
        ],
        ["git", "-C", repo_path, "branch", "--show-current"],
    ]
    for cmd in candidates:
        result = process_helpers.run_subprocess(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            continue
        value = (result.stdout or "").strip()
        if not value:
            continue
        remote_prefix = f"{remote_name}/"
        if value.startswith(remote_prefix):
            value = value[len(remote_prefix) :]
        return value or None
    return None


def resolve_canonical_origin_url_for_repo(repo_path: str, remote_name: str = "origin") -> str:
    remote_result = process_helpers.run_subprocess(
        ["git", "-C", repo_path, "remote", "get-url", remote_name],
        capture_output=True,
        text=True,
    )
    remote_url = (remote_result.stdout or "").strip() if remote_result.returncode == 0 else ""
    if not remote_url:
        return ""

    try:
        resolved_remote = str(Path(remote_url).resolve(strict=False))
    except OSError:
        resolved_remote = remote_url

    if "/runtime/repo-cache/" in resolved_remote and resolved_remote.endswith(".git"):
        mirror_origin_result = process_helpers.run_subprocess(
            ["git", f"--git-dir={resolved_remote}", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
        )
        mirror_origin_url = (
            (mirror_origin_result.stdout or "").strip()
            if mirror_origin_result.returncode == 0
            else ""
        )
        if mirror_origin_url:
            return mirror_origin_url

    return remote_url


def is_remote_git_source(source: str) -> bool:
    text = str(source or "").strip()
    if not text:
        return False
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        return True
    return bool(re.match(r"^[^/@:\s]+@[^:\s]+:.+", text))


def mirror_remote_name_for_repo(repo_path: str) -> str:
    current_branch_result = process_helpers.run_subprocess(
        ["git", "-C", repo_path, "branch", "--show-current"],
        capture_output=True,
        text=True,
    )
    current_branch = (
        (current_branch_result.stdout or "").strip()
        if current_branch_result.returncode == 0
        else ""
    )
    if current_branch:
        tracking_remote_result = process_helpers.run_subprocess(
            ["git", "-C", repo_path, "config", "--get", f"branch.{current_branch}.remote"],
            capture_output=True,
            text=True,
        )
        tracking_remote = (
            (tracking_remote_result.stdout or "").strip()
            if tracking_remote_result.returncode == 0
            else ""
        )
        if tracking_remote:
            return tracking_remote

    remote_head_result = process_helpers.run_subprocess(
        ["git", "-C", repo_path, "for-each-ref", "--format=%(refname)", "refs/remotes/*/HEAD"],
        capture_output=True,
        text=True,
    )
    if remote_head_result.returncode == 0:
        remote_heads = []
        for line in (remote_head_result.stdout or "").splitlines():
            remote_head_ref = line.strip()
            if not remote_head_ref:
                continue
            ref_parts = remote_head_ref.split("/")
            if (
                len(ref_parts) >= 4
                and ref_parts[:2] == ["refs", "remotes"]
                and ref_parts[-1] == "HEAD"
            ):
                remote_heads.append(ref_parts[2])
        if remote_heads:
            return remote_heads[0]

    remotes_result = process_helpers.run_subprocess(
        ["git", "-C", repo_path, "remote"],
        capture_output=True,
        text=True,
    )
    if remotes_result.returncode == 0:
        remotes = [
            line.strip() for line in (remotes_result.stdout or "").splitlines() if line.strip()
        ]
        if remotes:
            return remotes[0]

    return "origin"


def prune_pr_map(path: Path, *, session_ids: set[str]) -> tuple[int, set[tuple[str, str, str]]]:
    import json

    if not path.exists():
        return 0, set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, set()
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return 0, set()

    removed_streams: set[tuple[str, str, str]] = set()
    kept_entries: dict[str, object] = {}
    removed_count = 0
    for entry_key, raw_entry in entries.items():
        if not isinstance(raw_entry, dict):
            kept_entries[entry_key] = raw_entry
            continue
        session_id = str(raw_entry.get("session_id", "")).strip()
        if session_id in session_ids:
            provider = str(raw_entry.get("provider", "")).strip()
            repo = str(raw_entry.get("repo", "")).strip()
            pr_id = str(raw_entry.get("pr_id", "")).strip()
            if provider and repo and pr_id:
                removed_streams.add((provider, repo, pr_id))
            removed_count += 1
            continue
        kept_entries[entry_key] = raw_entry

    if removed_count:
        payload["entries"] = kept_entries
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return removed_count, removed_streams


def prune_pr_feedback_store(path: Path, *, streams: set[tuple[str, str, str]]) -> tuple[int, int]:
    if not streams:
        return 0, 0
    if not path.exists():
        return 0, 0
    return PrFeedbackStore(path).prune_streams(streams)


def remove_issue_bare_repo(
    issue_number: str, *, root_dir: Path, runtime_dir: Path
) -> tuple[bool, str | None]:
    git_root = resolve_git_root(root_dir)
    bare_repo_path = git_root / f"{issue_number}_repo.git"
    hook_log_paths = [
        root_dir / "runtime" / "hook-logs" / f"{bare_repo_path.name}.post-receive.log",
        runtime_dir / "hook-logs" / f"{bare_repo_path.name}.post-receive.log",
    ]
    trusted_repo_path: str | None = None
    if bare_repo_path.exists():
        try:
            config_result = subprocess.run(
                [
                    "git",
                    f"--git-dir={bare_repo_path}",
                    "config",
                    "--get",
                    "agent-sandbox.trustedRepoPath",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            config_result = None
        if config_result is not None and config_result.returncode == 0:
            trusted_repo_path = (config_result.stdout or "").strip() or None
        shutil.rmtree(bare_repo_path, ignore_errors=True)
    for hook_log_path in hook_log_paths:
        if hook_log_path.exists():
            hook_log_path.unlink()
    if trusted_repo_path:
        mirror_remote_name = f"{issue_number}-mirror"
        try:
            subprocess.run(
                ["git", "-C", trusted_repo_path, "remote", "remove", mirror_remote_name],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            pass
    return bare_repo_path.exists() is False, str(
        bare_repo_path
    ) if bare_repo_path.exists() else str(bare_repo_path)


_PROTECTED_MIRROR_HEAD_REFSPECS = PROTECTED_MIRROR_HEAD_REFSPECS
_resolve_git_root = resolve_git_root
_default_base_branch_for_repo = default_base_branch_for_repo
_default_base_branch_for_repo_remote = default_base_branch_for_repo_remote
_resolve_canonical_origin_url_for_repo = resolve_canonical_origin_url_for_repo
_is_remote_git_source = is_remote_git_source
_mirror_remote_name_for_repo = mirror_remote_name_for_repo
_prune_pr_map = prune_pr_map
_prune_pr_feedback_store = prune_pr_feedback_store
_remove_issue_bare_repo = remove_issue_bare_repo
