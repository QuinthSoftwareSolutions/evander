from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_sandbox.events.models import GitPushTriggerDefinition, TriggerEvent
from agent_sandbox.triggers.providers.base import TriggerHookHandler, TriggerProviderBase


class GitPushTriggerProvider(TriggerProviderBase):
    def __init__(self) -> None:
        super().__init__()
        self._handlers: list[TriggerHookHandler] = []

    def register_hook(self, handler: TriggerHookHandler) -> None:
        self._handlers.append(handler)

    def poll(self) -> list[TriggerEvent]:
        return []

    def handle_ref_update(self, *, repo_path: str, old_rev: str, new_rev: str, ref_name: str) -> TriggerEvent | None:
        trigger = self._trigger()
        if not git_push_trigger_matches_repo(trigger, repo_path):
            return None
        payload = build_git_push_payload(repo_path=repo_path, old_rev=old_rev, new_rev=new_rev, ref_name=ref_name)
        if payload is None:
            return None

        occurred_at = datetime.now(UTC)
        event = TriggerEvent(
            event_id=f"{trigger.id}:{payload['branch_name']}:{new_rev}",
            trigger_id=trigger.id,
            trigger_type=trigger.type,
            occurred_at=occurred_at,
            payload=payload,
        )
        for handler in self._handlers:
            handler(event)
        return event

    def _trigger(self) -> GitPushTriggerDefinition:
        if not isinstance(self.trigger_definition, GitPushTriggerDefinition):
            raise RuntimeError("GitPushTriggerProvider initialized with wrong definition")
        return self.trigger_definition


def git_push_trigger_matches_repo(trigger: GitPushTriggerDefinition | dict[str, Any], repo_path: str) -> bool:
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


def build_git_push_payload(*, repo_path: str, old_rev: str, new_rev: str, ref_name: str) -> dict[str, Any] | None:
    normalized_repo = str(Path(repo_path).expanduser().resolve())
    branch_name = _branch_name_from_ref(ref_name)
    if branch_name is None:
        return None
    payload: dict[str, Any] = {
        "branch_name": branch_name,
        "commit_hash": new_rev,
        "old_commit_hash": old_rev,
        "ref_name": ref_name,
        "repo_path": normalized_repo,
        "repo_name": Path(normalized_repo).name.removesuffix(".git"),
    }
    session_id = _extract_session_id(branch_name)
    if session_id:
        payload["session_id"] = session_id
        payload["review_branch"] = _derive_review_branch(branch_name)
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


def publish_git_push_event(*, runtime_dir: Path, repo_path: str, old_rev: str, new_rev: str, ref_name: str) -> None:
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
