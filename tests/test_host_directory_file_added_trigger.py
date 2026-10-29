from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter

from evander.admin.workflow_catalog import _WORKFLOW_EDITOR_NODE_CATALOG
from evander.admin.services.workflow_config_service import WorkflowConfigAdminService
from evander.events.models import (
    TriggerDefinitionDiscriminated,
    materialize_node_definition,
)
from config_helpers import canonical_workflow_node
from evander.events.provider_models import HostDirectoryFileAddedTriggerDefinition
from evander.host_directories.models import (
    HostDirectoryDefinition,
    HostDirectoryDirectoryConfig,
)
from evander.host_directories.paths import HOST_DIRECTORY_TRASH_NAME, host_directory_revision
from evander.triggers.providers import host_directory_file_added as provider_module
from evander.triggers.providers.base import TriggerRuntimeContext
from evander.triggers.providers.host_directory_file_added import (
    HostDirectoryFileAddedTriggerProvider,
    HostDirectoryScanError,
)
from evander.workflow.contracts import effective_node_output_schema


class _HostDirectoryResolver:
    def __init__(self, resources: dict[str, HostDirectoryDefinition]) -> None:
        self.resources = resources
        self.scopes: list[str | None] = []

    def resolve_host_directories_for_directory(
        self,
        directory_path: str | None,
    ) -> HostDirectoryDirectoryConfig:
        self.scopes.append(directory_path)
        return HostDirectoryDirectoryConfig(host_directories=self.resources)


def _resource(
    root: Path,
    *,
    disabled: bool = False,
    max_watch_entries: int = 10_000,
) -> HostDirectoryDefinition:
    return HostDirectoryDefinition(
        display_name="Inbox",
        description="Files delivered to the host inbox.",
        root=root,
        max_watch_entries=max_watch_entries,
        disabled=disabled,
    )


def _trigger(
    *,
    settle_seconds: float = 5,
    recursive: bool = False,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
) -> HostDirectoryFileAddedTriggerDefinition:
    return HostDirectoryFileAddedTriggerDefinition(
        id="watch-inbox",
        display_name="Watch inbox",
        type="trigger:host-directory:file-added",
        directory_id="inbox",
        recursive=recursive,
        include_globs=include_globs or [],
        exclude_globs=exclude_globs or [],
        settle_seconds=settle_seconds,
    )


def _provider(
    *,
    runtime_dir: Path,
    resolver: _HostDirectoryResolver,
    trigger: HostDirectoryFileAddedTriggerDefinition,
) -> HostDirectoryFileAddedTriggerProvider:
    provider = HostDirectoryFileAddedTriggerProvider()
    provider.initialize(
        trigger,
        TriggerRuntimeContext(
            resolved_config={"runtime_dir": runtime_dir},
            provider_config_resolver=resolver,
            metadata={"directory_path": "root/ops"},
        ),
    )
    return provider


def test_file_added_trigger_baselines_settles_retries_and_acknowledges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "inbox"
    root.mkdir()
    existing = root / "existing.txt"
    existing.write_text("existing", encoding="utf-8")
    runtime_dir = tmp_path / "runtime"
    resolver = _HostDirectoryResolver({"inbox": _resource(root)})
    trigger = _trigger()
    now = [datetime(2026, 7, 23, 8, 0, tzinfo=UTC)]
    monkeypatch.setattr(provider_module, "_utc_now", lambda: now[0])

    provider = _provider(runtime_dir=runtime_dir, resolver=resolver, trigger=trigger)
    assert provider.poll() == []
    assert resolver.scopes == ["root/ops", "root/ops"]

    existing.write_text("changed in place", encoding="utf-8")
    added = root / "new.txt"
    added.write_text("new", encoding="utf-8")
    now[0] += timedelta(seconds=1)
    assert provider.poll() == []

    now[0] += timedelta(seconds=4)
    assert provider.poll() == []

    # Providers are rebuilt each orchestrator cycle; candidate state remains durable.
    now[0] += timedelta(seconds=1)
    rebuilt = _provider(runtime_dir=runtime_dir, resolver=resolver, trigger=trigger)
    first = rebuilt.poll()
    assert len(first) == 1
    event = first[0]
    assert event.payload == {
        "directory_id": "inbox",
        "relative_path": "new.txt",
        "size": 3,
        "modified_at": event.payload["modified_at"],
        "revision": host_directory_revision(added.stat()),
        "detected_at": "2026-07-23T08:00:06Z",
    }

    # No acknowledgement models a queue publication failure: the exact event repeats,
    # even if the source changes before the retry.
    added.write_text("changed after detection", encoding="utf-8")
    retried = _provider(runtime_dir=runtime_dir, resolver=resolver, trigger=trigger).poll()
    assert retried == [event]

    rebuilt.acknowledge_published_event(event)
    assert rebuilt.poll() == []
    state = json.loads(
        (runtime_dir / "trigger-cursors-host-directories.json").read_text(encoding="utf-8")
    )[trigger.id]
    assert state["acknowledged_event_ids"] == [event.event_id]
    assert "new.txt" in state["known_files"]
    assert "new.txt" not in state["candidates"]
    assert state["next_generation"] == 2
    assert "generations" not in state


def test_file_added_trigger_ignores_known_modifications_and_emits_readds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "inbox"
    root.mkdir()
    runtime_dir = tmp_path / "runtime"
    resolver = _HostDirectoryResolver({"inbox": _resource(root)})
    trigger = _trigger(settle_seconds=0)
    now = [datetime(2026, 7, 23, 9, 0, tzinfo=UTC)]
    monkeypatch.setattr(provider_module, "_utc_now", lambda: now[0])
    provider = _provider(runtime_dir=runtime_dir, resolver=resolver, trigger=trigger)
    assert provider.poll() == []

    target = root / "item.txt"
    target.write_text("v1", encoding="utf-8")
    now[0] += timedelta(seconds=1)
    assert provider.poll() == []
    now[0] += timedelta(seconds=1)
    first = provider.poll()
    assert len(first) == 1
    provider.acknowledge_published_event(first[0])

    target.write_text("v2", encoding="utf-8")
    now[0] += timedelta(seconds=1)
    assert provider.poll() == []

    replacement = root / "replacement.txt"
    replacement.write_text("atomic replacement", encoding="utf-8")
    replacement.replace(target)
    now[0] += timedelta(seconds=1)
    assert provider.poll() == []
    now[0] += timedelta(seconds=1)
    assert provider.poll() == []

    target.unlink()
    now[0] += timedelta(seconds=1)
    assert provider.poll() == []
    target.write_text("v3", encoding="utf-8")
    now[0] += timedelta(seconds=1)
    assert provider.poll() == []
    now[0] += timedelta(seconds=1)
    second = provider.poll()

    assert len(second) == 1
    assert second[0].payload["relative_path"] == "item.txt"
    assert second[0].event_id != first[0].event_id


def test_file_added_trigger_filters_and_ignores_unsafe_entry_types(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "inbox"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    runtime_dir = tmp_path / "runtime"
    resolver = _HostDirectoryResolver({"inbox": _resource(root)})
    trigger = _trigger(
        settle_seconds=0,
        recursive=True,
        include_globs=["**/*.txt"],
        exclude_globs=["**/skip*"],
    )
    now = [datetime(2026, 7, 23, 10, 0, tzinfo=UTC)]
    monkeypatch.setattr(provider_module, "_utc_now", lambda: now[0])
    provider = _provider(runtime_dir=runtime_dir, resolver=resolver, trigger=trigger)
    assert provider.poll() == []

    nested = root / "nested"
    nested.mkdir()
    (nested / "ready.txt").write_text("ready", encoding="utf-8")
    (nested / "skip-this.txt").write_text("skip", encoding="utf-8")
    (root / "root.txt").write_text("root", encoding="utf-8")
    (root / "linked.txt").symlink_to(outside)
    os.mkfifo(root / "named-pipe.txt")
    trash = root / HOST_DIRECTORY_TRASH_NAME
    trash.mkdir()
    (trash / "hidden.txt").write_text("hidden", encoding="utf-8")
    nested_trash = nested / HOST_DIRECTORY_TRASH_NAME
    nested_trash.mkdir()
    (nested_trash / "ordinary.txt").write_text("ordinary", encoding="utf-8")

    now[0] += timedelta(seconds=1)
    assert provider.poll() == []
    now[0] += timedelta(seconds=1)
    events = provider.poll()

    assert [event.payload["relative_path"] for event in events] == [
        "nested/.evander-trash/ordinary.txt",
        "nested/ready.txt",
        "root.txt",
    ]


def test_scan_failure_preserves_durable_cursor_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "inbox"
    root.mkdir()
    (root / "baseline.txt").write_text("baseline", encoding="utf-8")
    runtime_dir = tmp_path / "runtime"
    resolver = _HostDirectoryResolver({"inbox": _resource(root, max_watch_entries=2)})
    trigger = _trigger()
    now = [datetime(2026, 7, 23, 11, 0, tzinfo=UTC)]
    monkeypatch.setattr(provider_module, "_utc_now", lambda: now[0])
    provider = _provider(runtime_dir=runtime_dir, resolver=resolver, trigger=trigger)
    assert provider.poll() == []
    cursor_path = runtime_dir / "trigger-cursors-host-directories.json"
    before = cursor_path.read_bytes()

    (root / "one.txt").write_text("one", encoding="utf-8")
    (root / "two.txt").write_text("two", encoding="utf-8")
    with pytest.raises(HostDirectoryScanError, match="entry limit"):
        provider.poll()
    assert cursor_path.read_bytes() == before

    moved = tmp_path / "moved-inbox"
    root.rename(moved)
    with pytest.raises(HostDirectoryScanError, match="root is unavailable"):
        provider.poll()
    assert cursor_path.read_bytes() == before


def test_scan_rejects_regular_file_mount_boundary() -> None:
    with pytest.raises(
        HostDirectoryScanError,
        match="mount-boundary crossing for file",
    ):
        provider_module._ensure_same_device(
            SimpleNamespace(st_dev=202),
            root_device=101,
            entry_kind="file",
        )


def test_scan_rejects_same_device_entry_with_distinct_mount_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provider_module, "_mount_id", lambda _descriptor: 202)
    with pytest.raises(
        HostDirectoryScanError,
        match="mount-boundary crossing for file",
    ):
        provider_module._ensure_same_mount(
            10,
            root_mount_id=101,
            entry_kind="file",
        )


def test_file_added_settle_resets_when_ctime_revision_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "inbox"
    root.mkdir()
    runtime_dir = tmp_path / "runtime"
    resolver = _HostDirectoryResolver({"inbox": _resource(root)})
    trigger = _trigger(settle_seconds=5)
    now = [datetime(2026, 7, 23, 12, 0, tzinfo=UTC)]
    monkeypatch.setattr(provider_module, "_utc_now", lambda: now[0])
    provider = _provider(runtime_dir=runtime_dir, resolver=resolver, trigger=trigger)
    assert provider.poll() == []

    target = root / "item.txt"
    target.write_text("stable size", encoding="utf-8")
    now[0] += timedelta(seconds=1)
    assert provider.poll() == []

    os.chmod(target, 0o600)
    now[0] += timedelta(seconds=5)
    assert provider.poll() == []

    now[0] += timedelta(seconds=5)
    events = provider.poll()
    assert [event.payload["relative_path"] for event in events] == ["item.txt"]


def test_trigger_model_and_catalog_expose_host_directory_fields() -> None:
    adapter = TypeAdapter(TriggerDefinitionDiscriminated)
    trigger = materialize_node_definition(
        adapter.validate_python(
            canonical_workflow_node(
                {
                    "id": "watch",
                    "display_name": "Watch",
                    "type": "trigger:host-directory:file-added",
                    "config": {
                        "directory_id": " INBOX ",
                        "path": ".hidden/incoming",
                        "recursive": True,
                        "include_globs": [" *.pdf ", ""],
                        "exclude_globs": ["archive/**"],
                    },
                }
            )
        )
    )

    assert isinstance(trigger, HostDirectoryFileAddedTriggerDefinition)
    assert trigger.directory_id == "inbox"
    assert trigger.path == ".hidden/incoming"
    assert trigger.include_globs == ["*.pdf"]
    assert trigger.settle_seconds == 5
    assert effective_node_output_schema(trigger) == {
        "directory_id": {"type": "string"},
        "relative_path": {"type": "string"},
        "size": {"type": "integer"},
        "modified_at": {"type": "string"},
        "revision": {"type": "string"},
        "detected_at": {"type": "string"},
    }

    catalog_entry = next(
        entry
        for entry in _WORKFLOW_EDITOR_NODE_CATALOG
        if entry.type == "trigger:host-directory:file-added"
    )
    assert [field.key for field in catalog_entry.fields] == [
        "directory_id",
        "path",
        "recursive",
        "include_globs",
        "exclude_globs",
        "settle_seconds",
    ]

    with pytest.raises(ValueError, match="traversal"):
        HostDirectoryFileAddedTriggerDefinition(
            id="bad",
            display_name="Bad",
            type="trigger:host-directory:file-added",
            directory_id="inbox",
            path="../escape",
        )


def test_catalog_host_directory_options_omit_roots_and_disabled_resources(
    tmp_path: Path,
) -> None:
    effective_config = {
        "host_directories": {
            "inbox": _resource(tmp_path / "private-inbox").model_dump(mode="json"),
            "disabled": _resource(
                tmp_path / "private-disabled",
                disabled=True,
            ).model_dump(mode="json"),
        }
    }
    fake_service = SimpleNamespace(
        _normalize_directory_reference=lambda value: value or "root",
        directory_admin=SimpleNamespace(
            compute_effective_config=lambda path, domain: SimpleNamespace(
                ok=True,
                data=SimpleNamespace(resolution=SimpleNamespace(effective_config=effective_config)),
                errors=[],
            )
        ),
    )

    options = WorkflowConfigAdminService._directory_resource_trigger_options(
        fake_service,
        "root/ops",
    )

    assert [option.value for option in options] == ["inbox"]
    assert options[0].label == "Inbox"
    assert options[0].description == "Files delivered to the host inbox."
    assert str(tmp_path) not in json.dumps([option.model_dump(mode="json") for option in options])


def test_trigger_rejects_unknown_or_disabled_directory(
    tmp_path: Path,
) -> None:
    trigger = _trigger()
    runtime_dir = tmp_path / "runtime"

    with pytest.raises(ValueError, match="Unknown host directory"):
        _provider(
            runtime_dir=runtime_dir,
            resolver=_HostDirectoryResolver({}),
            trigger=trigger,
        )

    with pytest.raises(ValueError, match="disabled"):
        _provider(
            runtime_dir=runtime_dir,
            resolver=_HostDirectoryResolver(
                {"inbox": _resource(tmp_path / "disabled", disabled=True)}
            ),
            trigger=trigger,
        )
