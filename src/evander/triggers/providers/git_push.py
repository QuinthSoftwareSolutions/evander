from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evander.events.models import TriggerEvent
from evander.events.provider_models import GitPushTriggerDefinition
from evander.git_refs import logical_branch_name
from evander.git_pr_notes import (
    agent_pr_notes_ref_for_session,
    is_agent_pr_notes_ref,
)
from evander.triggers.providers.base import TriggerHookHandler, TriggerProviderBase


class GitPushTriggerProvider(TriggerProviderBase):
    def __init__(self) -> None:
        super().__init__()
        self._handlers: list[TriggerHookHandler] = []

    def register_hook(self, handler: TriggerHookHandler) -> None:
        self._handlers.append(handler)

    def poll(self) -> list[TriggerEvent]:
        return []

    def handle_ref_update(
        self, *, repo_path: str, old_rev: str, new_rev: str, ref_name: str
    ) -> TriggerEvent | None:
        trigger = self._trigger()
        repo_metadata = _load_managed_repo_metadata(Path(repo_path).expanduser().resolve())
        managed_repo_id = repo_metadata.get("managed_repo_id")
        if not managed_repo_id or not git_push_trigger_matches_repo(trigger, managed_repo_id):
            return None
        owner_directory = repo_metadata.get("directory_path")
        workflow_directory = str(
            (self.runtime_context.metadata if self.runtime_context else {}).get("directory_path")
            or "root"
        )
        if not owner_directory or not (
            workflow_directory == owner_directory
            or workflow_directory.startswith(f"{owner_directory}/")
        ):
            return None
        payloads = build_git_push_payloads(
            repo_path=repo_path,
            old_rev=old_rev,
            new_rev=new_rev,
            ref_name=ref_name,
            managed_repo_id=managed_repo_id,
            default_branch=repo_metadata.get("default_branch"),
        )
        if not payloads:
            return None

        occurred_at = datetime.now(UTC)
        events: list[TriggerEvent] = []
        for payload in payloads:
            event = TriggerEvent(
                event_id=f"{trigger.id}:{payload['branch_name']}:{payload.get('event_hash') or new_rev}",
                trigger_id=trigger.id,
                trigger_type=trigger.type,
                occurred_at=occurred_at,
                payload=payload,
            )
            events.append(event)
            for handler in self._handlers:
                handler(event)
        return events[0] if events else None

    def _trigger(self) -> GitPushTriggerDefinition:
        if not isinstance(self.trigger_definition, GitPushTriggerDefinition):
            raise RuntimeError("GitPushTriggerProvider initialized with wrong definition")
        return self.trigger_definition


def git_push_trigger_matches_repo(
    trigger: GitPushTriggerDefinition | dict[str, Any], managed_repo_id: str
) -> bool:
    normalized_repo_id = _managed_repo_id_from_value(managed_repo_id)
    if normalized_repo_id is None:
        return False
    if isinstance(trigger, GitPushTriggerDefinition):
        configured_repo_id = trigger.managed_repo_id
        repo_id_glob = trigger.managed_repo_id_glob
    else:
        configured_repo_id = _normalize_optional_string(trigger.get("managed_repo_id"))
        repo_id_glob = _normalize_optional_string(trigger.get("managed_repo_id_glob"))
    if configured_repo_id:
        return normalized_repo_id == configured_repo_id
    if repo_id_glob:
        return fnmatch.fnmatchcase(normalized_repo_id, repo_id_glob)
    return False


def build_git_push_payload(
    *,
    repo_path: str,
    old_rev: str,
    new_rev: str,
    ref_name: str,
    managed_repo_id: str | None = None,
    default_branch: str | None = None,
) -> dict[str, Any] | None:
    payloads = build_git_push_payloads(
        repo_path=repo_path,
        old_rev=old_rev,
        new_rev=new_rev,
        ref_name=ref_name,
        managed_repo_id=managed_repo_id,
        default_branch=default_branch,
    )
    if not payloads:
        return None
    return payloads[0]


def build_git_push_payloads(
    *,
    repo_path: str,
    old_rev: str,
    new_rev: str,
    ref_name: str,
    managed_repo_id: str | None = None,
    default_branch: str | None = None,
) -> list[dict[str, Any]]:
    normalized_repo = str(Path(repo_path).expanduser().resolve())
    repo_metadata = _load_managed_repo_metadata(Path(normalized_repo))
    resolved_managed_repo_id = _normalize_optional_string(managed_repo_id) or repo_metadata.get(
        "managed_repo_id"
    )
    if resolved_managed_repo_id is None:
        return []
    resolved_default_branch = _normalize_optional_string(default_branch) or repo_metadata.get(
        "default_branch"
    )
    branch_name = _branch_name_from_ref(ref_name)
    if branch_name is not None:
        return [
            _build_branch_payload(
                managed_repo_id=resolved_managed_repo_id,
                default_branch=resolved_default_branch,
                branch_name=branch_name,
                old_rev=old_rev,
                new_rev=new_rev,
                ref_name=ref_name,
            )
        ]

    if is_agent_pr_notes_ref(ref_name):
        return _build_note_push_payloads(
            normalized_repo=normalized_repo,
            managed_repo_id=resolved_managed_repo_id,
            default_branch=resolved_default_branch,
            old_rev=old_rev,
            new_rev=new_rev,
            ref_name=ref_name,
        )

    return []


def _build_branch_payload(
    *,
    managed_repo_id: str,
    default_branch: str | None,
    branch_name: str,
    old_rev: str,
    new_rev: str,
    ref_name: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "managed_repo_id": managed_repo_id,
        "branch_name": branch_name,
        "commit_hash": new_rev,
        "new_commit_hash": new_rev,
        "event_hash": new_rev,
        "old_commit_hash": old_rev,
        "ref_name": ref_name,
        "default_branch": default_branch,
    }
    session_id = (
        _extract_session_id(branch_name)
        if ref_name.startswith("refs/evander/agents/") or is_agent_pr_notes_ref(ref_name)
        else None
    )
    if session_id:
        payload["session_id"] = session_id
        payload["review_branch"] = _derive_review_branch(branch_name)
        payload["notes_ref"] = agent_pr_notes_ref_for_session(session_id)
    return payload


def _branch_name_from_ref(ref_name: str) -> str | None:
    return logical_branch_name(ref_name.strip())


def _extract_session_id(branch_name: str) -> str | None:
    normalized = branch_name.strip()
    if not normalized.startswith("agents/"):
        return None
    remainder = normalized.removeprefix("agents/").strip()
    if not remainder or "/" not in remainder:
        return None
    session_id = remainder.split("/", 1)[0].strip()
    return session_id or None


def _derive_review_branch(branch_name: str) -> str:
    normalized = branch_name.strip()
    if normalized.startswith("agents/") and normalized.count("/") >= 2:
        return f"review/{normalized.removeprefix('agents/')}"
    return f"review/{normalized}"


def _build_note_push_payloads(
    *,
    normalized_repo: str,
    managed_repo_id: str,
    default_branch: str | None,
    old_rev: str,
    new_rev: str,
    ref_name: str,
) -> list[dict[str, Any]]:
    repo_path = Path(normalized_repo)
    changed_targets = _list_changed_note_targets(
        repo_path=repo_path, old_rev=old_rev, new_rev=new_rev
    )
    if not changed_targets:
        return []

    branches = _list_agent_branch_tips(repo_path)
    payloads: list[dict[str, Any]] = []
    for branch_name, tip_sha in branches:
        if tip_sha not in changed_targets:
            continue
        session_id = _extract_session_id(branch_name)
        if session_id is None or agent_pr_notes_ref_for_session(session_id) != ref_name:
            continue
        payload = _build_branch_payload(
            managed_repo_id=managed_repo_id,
            default_branch=default_branch,
            branch_name=branch_name,
            old_rev=old_rev,
            new_rev=tip_sha,
            ref_name=ref_name,
        )
        payload["event_hash"] = new_rev
        payload["notes_ref"] = ref_name
        payload["note_target_commit_hash"] = tip_sha
        payload["note_ref_new_rev"] = new_rev
        payload["note_ref_old_rev"] = old_rev
        payloads.append(payload)
    return payloads


def _list_changed_note_targets(*, repo_path: Path, old_rev: str, new_rev: str) -> set[str]:
    zero_sha = "0" * 40
    if new_rev == zero_sha:
        return set()

    if old_rev == zero_sha:
        completed = subprocess.run(
            ["git", f"--git-dir={repo_path}", "ls-tree", "-r", "--name-only", new_rev],
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        completed = subprocess.run(
            [
                "git",
                f"--git-dir={repo_path}",
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                old_rev,
                new_rev,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    if completed.returncode != 0:
        return set()
    targets: set[str] = set()
    for raw_path in completed.stdout.splitlines():
        target = _note_target_from_path(raw_path)
        if target:
            targets.add(target)
    return targets


def _note_target_from_path(path_text: str) -> str | None:
    normalized = path_text.strip().replace("/", "")
    if len(normalized) != 40:
        return None
    if any(char not in "0123456789abcdefABCDEF" for char in normalized):
        return None
    return normalized.lower()


def _list_agent_branch_tips(repo_path: Path) -> list[tuple[str, str]]:
    completed = subprocess.run(
        [
            "git",
            f"--git-dir={repo_path}",
            "for-each-ref",
            "--format=%(refname:strip=2) %(objectname)",
            "refs/evander/agents",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return []
    branches: list[tuple[str, str]] = []
    for line in completed.stdout.splitlines():
        branch_name, _, object_name = line.strip().partition(" ")
        if not branch_name or not object_name:
            continue
        branches.append((branch_name, object_name.strip()))
    return branches


def _load_managed_repo_metadata(repo_path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    config_mappings = {
        "managed_repo_id": "evander.managedRepoId",
        "default_branch": "evander.baseBranch",
        "directory_path": "evander.directoryPath",
    }
    for payload_key, config_key in config_mappings.items():
        value = _git_config_get(repo_path, config_key)
        if value:
            metadata[payload_key] = value
    return metadata


def _managed_repo_id_from_value(value: object) -> str | None:
    candidate = _normalize_optional_string(value)
    if candidate is None:
        return None
    if candidate.startswith("mr_"):
        return candidate
    return _load_managed_repo_metadata(Path(candidate).expanduser().resolve()).get(
        "managed_repo_id"
    )


def _git_config_get(repo_path: Path, key: str) -> str | None:
    completed = subprocess.run(
        ["git", f"--git-dir={repo_path}", "config", "--get", key],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return _normalize_optional_string(completed.stdout)


def _normalize_optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def publish_git_push_event(
    *, runtime_dir: Path, repo_path: str, old_rev: str, new_rev: str, ref_name: str
) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    normalized_repo = Path(repo_path).expanduser().resolve()
    managed_repo_id = _load_managed_repo_metadata(normalized_repo).get("managed_repo_id")
    if managed_repo_id is None:
        raise RuntimeError(
            "Git push source is not a registered managed repository; register it with "
            "`evander-cli git-repos register` before restarting workers"
        )
    event_payload = {
        "managed_repo_id": managed_repo_id,
        "old_commit_hash": old_rev,
        "new_commit_hash": new_rev,
        "ref_name": ref_name,
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    path = runtime_dir / "trigger-git-push-events.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event_payload, sort_keys=True) + "\n")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish git push trigger hook events.")
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--old-rev", required=True)
    parser.add_argument("--new-rev", required=True)
    parser.add_argument("--ref-name", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    publish_git_push_event(
        runtime_dir=Path(args.runtime_dir),
        repo_path=args.repo_path,
        old_rev=args.old_rev,
        new_rev=args.new_rev,
        ref_name=args.ref_name,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
