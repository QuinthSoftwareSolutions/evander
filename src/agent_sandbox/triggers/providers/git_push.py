from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.events.provider_models import GitPushTriggerDefinition
from agent_sandbox.git_pr_notes import (
    agent_pr_notes_ref_for_session,
    is_agent_pr_notes_ref,
)
from agent_sandbox.triggers.providers.base import TriggerHookHandler, TriggerProviderBase


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
        if not git_push_trigger_matches_repo(trigger, repo_path):
            return None
        payloads = build_git_push_payloads(
            repo_path=repo_path, old_rev=old_rev, new_rev=new_rev, ref_name=ref_name
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
    trigger: GitPushTriggerDefinition | dict[str, Any], repo_path: str
) -> bool:
    normalized_repo = str(Path(repo_path).expanduser().resolve())
    if isinstance(trigger, GitPushTriggerDefinition):
        path_to_repo = trigger.path_to_repo
        path_glob = trigger.path_glob
    else:
        path_to_repo = _normalize_optional_string(trigger.get("path_to_repo"))
        path_glob = _normalize_optional_string(trigger.get("path_glob"))
    if path_to_repo:
        return normalized_repo == str(Path(path_to_repo).expanduser().resolve())
    if path_glob:
        return fnmatch.fnmatch(normalized_repo, path_glob)
    return False


def build_git_push_payload(
    *, repo_path: str, old_rev: str, new_rev: str, ref_name: str
) -> dict[str, Any] | None:
    payloads = build_git_push_payloads(
        repo_path=repo_path, old_rev=old_rev, new_rev=new_rev, ref_name=ref_name
    )
    if not payloads:
        return None
    return payloads[0]


def build_git_push_payloads(
    *, repo_path: str, old_rev: str, new_rev: str, ref_name: str
) -> list[dict[str, Any]]:
    normalized_repo = str(Path(repo_path).expanduser().resolve())
    branch_name = _branch_name_from_ref(ref_name)
    if branch_name is not None:
        return [
            _build_branch_payload(
                normalized_repo=normalized_repo,
                branch_name=branch_name,
                old_rev=old_rev,
                new_rev=new_rev,
                ref_name=ref_name,
            )
        ]

    if is_agent_pr_notes_ref(ref_name):
        return _build_note_push_payloads(
            normalized_repo=normalized_repo, old_rev=old_rev, new_rev=new_rev, ref_name=ref_name
        )

    return []


def _build_branch_payload(
    *, normalized_repo: str, branch_name: str, old_rev: str, new_rev: str, ref_name: str
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "branch_name": branch_name,
        "commit_hash": new_rev,
        "event_hash": new_rev,
        "old_commit_hash": old_rev,
        "ref_name": ref_name,
        "repo_path": normalized_repo,
        "repo_name": Path(normalized_repo).name.removesuffix(".git"),
    }
    session_id = _extract_session_id(branch_name)
    if session_id:
        payload["session_id"] = session_id
        payload["review_branch"] = _derive_review_branch(branch_name)
        payload["notes_ref"] = agent_pr_notes_ref_for_session(session_id)
    payload.update(_load_repo_metadata(Path(normalized_repo)))
    return payload


def _branch_name_from_ref(ref_name: str) -> str | None:
    normalized = ref_name.strip()
    prefix = "refs/heads/"
    if not normalized.startswith(prefix):
        return None
    branch_name = normalized.removeprefix(prefix).strip()
    return branch_name or None


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
    *, normalized_repo: str, old_rev: str, new_rev: str, ref_name: str
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
        payload = _build_branch_payload(
            normalized_repo=normalized_repo,
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
            "refs/heads/agents",
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


def _load_repo_metadata(repo_path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    config_mappings = {
        "trusted_repo_path": "agent-sandbox.trustedRepoPath",
        "base_branch": "agent-sandbox.baseBranch",
        "canonical_origin_url": "agent-sandbox.canonicalOriginUrl",
    }
    for payload_key, config_key in config_mappings.items():
        value = _git_config_get(repo_path, config_key)
        if value:
            metadata[payload_key] = value
    return metadata


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
    event_payload = {
        "repo_path": str(Path(repo_path).resolve()),
        "old_rev": old_rev,
        "new_rev": new_rev,
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
