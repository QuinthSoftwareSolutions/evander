from __future__ import annotations

from evander.execution import (
    ExecutionBackendKind,
    ExecutionSpec,
)


def test_execution_spec_defaults_and_metadata_preservation() -> None:
    spec = ExecutionSpec(
        target_name=" vm-test ",
        session_id=" session-1 ",
        image=" runner:latest ",
        repo_source=" git@example.test/repo.git ",
        clone_branch=" main ",
        persistence_mode=" session ",
        workspace_mode=" fresh ",
        metadata={"vm_name": "vm-test", "custom": {"a": 1}},
    )

    assert spec.backend == ExecutionBackendKind.LIBVIRT_VM
    assert spec.target_name == "vm-test"
    assert spec.session_id == "session-1"
    assert spec.image == "runner:latest"
    assert spec.repo_source == "git@example.test/repo.git"
    assert spec.clone_branch == "main"
    assert spec.persistence_mode == "session"
    assert spec.workspace_mode == "fresh"
    assert spec.directory_path == "root"
    assert spec.metadata == {"vm_name": "vm-test", "custom": {"a": 1}}
