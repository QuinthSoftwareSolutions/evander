from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from agent_sandbox.triggers.models import GitPushTriggerDefinition, TriggerEvent
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
        normalized_repo = str(Path(repo_path).resolve())
        if normalized_repo != str(Path(trigger.path_to_repo).resolve()):
            return None
        branch_name = _branch_name_from_ref(ref_name)
        if branch_name is None:
            return None

        occurred_at = datetime.now(UTC)
        event = TriggerEvent(
            event_id=f"{trigger.id}:{branch_name}:{new_rev}",
            trigger_id=trigger.id,
            trigger_type=trigger.type,
            occurred_at=occurred_at,
            payload={
                "branch_name": branch_name,
                "commit_hash": new_rev,
                "old_commit_hash": old_rev,
                "ref_name": ref_name,
                "repo_path": normalized_repo,
            },
        )
        for handler in self._handlers:
            handler(event)
        return event

    def _trigger(self) -> GitPushTriggerDefinition:
        if not isinstance(self.trigger_definition, GitPushTriggerDefinition):
            raise RuntimeError("GitPushTriggerProvider initialized with wrong definition")
        return self.trigger_definition


def _branch_name_from_ref(ref_name: str) -> str | None:
    normalized = ref_name.strip()
    prefix = "refs/heads/"
    if not normalized.startswith(prefix):
        return None
    branch_name = normalized.removeprefix(prefix).strip()
    return branch_name or None


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
