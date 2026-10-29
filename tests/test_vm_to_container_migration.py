from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from evander.config import GatewayConfig
from evander.models import SessionManifest, ToolName
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.vm_to_container_migration import (
    CONTAINER_BACKEND,
    build_container_migrated_manifest,
    build_session_migration_plan,
    create_migration_backup,
    cutover_sessions_to_container,
    ensure_worker_session_paths,
    is_libvirt_backed_session,
    reconcile_codex_state,
    refresh_container_session_files,
    restore_selected_manifests_from_backup,
    select_sessions_for_migration,
    verify_worker_session_state,
)


def _session(
    session_id: str = "session-1",
    *,
    vm_id: str | None = "vm-session-1",
    vm_provider: str | None = "libvirt",
    metadata: dict[str, object] | None = None,
) -> SessionManifest:
    return SessionManifest(
        session_id=session_id,
        vm_id=vm_id,
        vm_provider=vm_provider,
        model="gpt-5.4",
        directory_path="root/team",
        allowed_tools=[ToolName.PROMPT_AGENT, ToolName.RESPOND_TO_AGENT],
        approval_required_tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) + timedelta(days=1),
        metadata=dict(metadata or {}),
    )


def test_migrated_manifest_preserves_agent_metadata_and_records_previous_vm() -> None:
    source = _session(
        metadata={
            "agent_provider": "codex",
            "agent_thread_ids": ["thread-1"],
            "team_addon": {"agent_id": "agent-a"},
            "execution_backend": "libvirt_vm",
        }
    )
    migrated_at = datetime(2026, 6, 26, 12, 0, tzinfo=UTC)

    migrated = build_container_migrated_manifest(
        source,
        migrated_at=migrated_at,
        backup_id="backup-1",
    )

    assert migrated.vm_id is None
    assert migrated.vm_provider == CONTAINER_BACKEND
    assert migrated.vm_provider_id == "session-1"
    assert migrated.metadata["agent_provider"] == "codex"
    assert migrated.metadata["agent_thread_ids"] == ["thread-1"]
    assert migrated.metadata["team_addon"] == {"agent_id": "agent-a"}
    assert migrated.metadata["execution_backend"] == CONTAINER_BACKEND
    assert migrated.metadata["target_name"] == "session-1"
    assert migrated.metadata["migrated_from_libvirt_at"] == migrated_at.isoformat()
    assert migrated.metadata["migration_backup_id"] == "backup-1"
    assert migrated.metadata["previous_libvirt"]["vm_id"] == "vm-session-1"
    assert migrated.metadata["previous_libvirt"]["vm_provider"] == "libvirt"
    assert migrated.allowed_tools == source.allowed_tools
    assert migrated.approval_required_tools == source.approval_required_tools


def test_select_all_only_includes_libvirt_backed_sessions(tmp_path: Path) -> None:
    libvirt_session = _session("libvirt-session")
    container_session = _session(
        "container-session",
        vm_id=None,
        vm_provider=CONTAINER_BACKEND,
        metadata={"execution_backend": CONTAINER_BACKEND},
    )
    unknown_session = _session("unknown-session", vm_id=None, vm_provider=None)
    sessions = {
        session.session_id: session
        for session in (libvirt_session, container_session, unknown_session)
    }

    selected = select_sessions_for_migration(sessions, all_sessions=True)
    plan = build_session_migration_plan(libvirt_session, worker_root=tmp_path / "worker")

    assert [session.session_id for session in selected] == ["libvirt-session"]
    assert is_libvirt_backed_session(libvirt_session)
    assert not is_libvirt_backed_session(container_session)
    assert plan.eligible
    assert plan.source_vm_name == "vm-session-1"


def test_cutover_backup_and_rollback_restore_original_manifest(tmp_path: Path) -> None:
    repository = JsonSessionManifestRepository(tmp_path / "sessions.json")
    original = _session(metadata={"agent_provider": "codex", "agent_thread_ids": ["t1"]})
    repository.upsert_session(original)
    backup = create_migration_backup(
        repository,
        [original],
        backup_root=tmp_path / "backups",
        created_at=datetime(2026, 6, 26, 12, 0, tzinfo=UTC),
    )

    cutover_sessions_to_container(repository, [original], backup_id=backup.backup_id)

    cutover = repository.get_session("session-1")
    assert cutover is not None
    assert cutover.vm_provider == CONTAINER_BACKEND

    restored_ids = restore_selected_manifests_from_backup(repository, backup.path)

    restored = repository.get_session("session-1")
    assert restored_ids == ["session-1"]
    assert restored is not None
    assert restored.vm_id == "vm-session-1"
    assert restored.vm_provider == "libvirt"
    assert restored.metadata == {"agent_provider": "codex", "agent_thread_ids": ["t1"]}


def test_verify_worker_session_state_and_codex_state_mirror(tmp_path: Path) -> None:
    worker_root = tmp_path / "worker"

    missing = verify_worker_session_state(worker_root=worker_root, session_id="session-1")
    assert not missing.ok
    assert any("agent_home is missing" in error for error in missing.errors)

    paths = ensure_worker_session_paths(worker_root, "session-1")
    legacy_session = paths.agent_home / ".codex" / "sessions" / "2026" / "thread.jsonl"
    legacy_session.parent.mkdir(parents=True)
    legacy_session.write_text('{"type":"message"}\n', encoding="utf-8")

    warnings = reconcile_codex_state(paths.agent_home)
    verified = verify_worker_session_state(worker_root=worker_root, session_id="session-1")

    assert warnings == []
    assert (
        paths.agent_home / ".config" / "evander" / "codex" / "sessions" / "2026" / "thread.jsonl"
    ).read_text(encoding="utf-8") == '{"type":"message"}\n'
    assert verified.ok
    assert {path.name for path in verified.codex_session_roots} == {"sessions"}


def test_refresh_container_session_files_preserves_original_profile(tmp_path: Path) -> None:
    agent_home = tmp_path / "agent_home"
    agent_home.mkdir()
    profile = agent_home / ".profile"
    profile.write_text("original profile\n", encoding="utf-8")
    config = GatewayConfig(
        runtime_dir=tmp_path / "runtime",
        execution_backend="container_worker_vm",
        gateway_url="http://10.0.0.1:8000/mcp",
        container_worker_base_url="http://worker.test",
    )
    session = _session(metadata={"agent_provider": "codex"})

    warnings = refresh_container_session_files(
        config=config,
        session=session,
        agent_home=agent_home,
    )

    preserved = agent_home / ".evander" / "migration-originals" / ".profile"
    assert preserved.read_text(encoding="utf-8") == "original profile\n"
    assert "EVANDER_SESSION_ID=session-1" in (agent_home / ".evander" / "session.env").read_text(
        encoding="utf-8"
    )
    assert "evander_openai_relay" in (
        agent_home / ".config" / "evander" / "codex" / "config.toml"
    ).read_text(encoding="utf-8")
    assert "non_prefixed_mcp_tool_names" not in (
        agent_home / ".config" / "evander" / "codex" / "config.toml"
    ).read_text(encoding="utf-8")
    assert "non_prefixed_mcp_tool_names" not in (agent_home / ".codex" / "config.toml").read_text(
        encoding="utf-8"
    )
    assert any("preserved original" in warning for warning in warnings)
