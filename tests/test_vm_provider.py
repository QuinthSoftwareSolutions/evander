from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from evander.models import SessionManifest
from evander.models import VmSummary
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.vm.power_state import normalize_power_state
from evander.vm.provider import (
    AzureVmProvider,
    VmProviderContext,
    VmProviderRegistry,
    session_vm_provider,
)


def test_normalize_power_state_supports_libvirt_and_azure_values() -> None:
    assert normalize_power_state("running") == "running"
    assert normalize_power_state("shut off") == "stopped"
    assert normalize_power_state("PowerState/deallocated") == "stopped"
    assert normalize_power_state("crashed") == "failed"


def test_session_vm_provider_defaults_to_libvirt() -> None:
    manifest = SessionManifest(
        session_id="session-1",
        vm_id="vm-1",
        allowed_tools=[],
        expires_at="2099-01-01T00:00:00Z",
    )
    assert session_vm_provider(manifest) == "libvirt"

    azure_manifest = manifest.model_copy(update={"vm_provider": "azure"})
    assert session_vm_provider(azure_manifest) == "azure"


@dataclass
class _AzureClientStub:
    payload: list[dict[str, Any]]

    def list_vms(self, *, config: Any, include_inactive: bool = True) -> list[dict[str, Any]]:
        del config, include_inactive
        return list(self.payload)

    def create_vm(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("not used")

    def start_vm(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("not used")

    def stop_vm(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("not used")

    def resolve_private_ip(self, **kwargs: Any) -> str | None:
        raise AssertionError("not used")


def test_azure_provider_lists_provider_neutral_vm_summaries(tmp_path: Path) -> None:
    session_repository = JsonSessionManifestRepository(tmp_path / "sessions.json")
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-azure",
            vm_id="vm-azure-1",
            vm_provider="azure",
            allowed_tools=[],
            directory_path="root/team",
            expires_at="2099-01-01T00:00:00Z",
        )
    )
    dependencies = SimpleNamespace(
        runtime=SimpleNamespace(session_repository=session_repository),
        normalize_directory_path=lambda directory_path: directory_path or "root",
        resolve_vm_defaults=lambda directory_path: SimpleNamespace(
            azure=SimpleNamespace(resource_group="rg-agents"),
        ),
        direct_owned_entity_ids=lambda directory_path, kind="vm": {"vm-azure-1"},
        agent_control_store=SimpleNamespace(status_for_vm=lambda vm_name: "enabled"),
    )
    provider = AzureVmProvider(
        VmProviderContext(dependencies=dependencies),
        client=_AzureClientStub(
            payload=[
                {
                    "name": "vm-azure-1",
                    "powerState": "PowerState/running",
                    "privateIps": "10.0.0.5",
                }
            ]
        ),
    )

    summaries = provider.list_vms(directory_path="root/team", direct_only=True)

    assert len(summaries) == 1
    assert summaries[0].provider == "azure"
    assert summaries[0].power_state == "running"
    assert summaries[0].provider_state == "PowerState/running"
    assert summaries[0].ip_address == "10.0.0.5"


def test_provider_registry_keeps_successful_provider_results_when_another_provider_fails() -> None:
    class FailingProvider:
        def list_vms(self, **kwargs: Any) -> list[VmSummary]:
            del kwargs
            raise RuntimeError("libvirt unavailable")

    class StaticProvider:
        def list_vms(self, **kwargs: Any) -> list[VmSummary]:
            del kwargs
            return [
                VmSummary(
                    vm_name="vm-azure-1",
                    provider="azure",
                    power_state="running",
                    agent_state="omitted",
                )
            ]

    registry = VmProviderRegistry(SimpleNamespace())
    registry._providers = {  # type: ignore[attr-defined]
        "libvirt": FailingProvider(),  # type: ignore[dict-item]
        "azure": StaticProvider(),  # type: ignore[dict-item]
    }

    summaries = registry.list_vms()

    assert [summary.vm_name for summary in summaries] == ["vm-azure-1"]


def test_provider_registry_raises_when_all_provider_inventory_fails() -> None:
    class FailingProvider:
        def list_vms(self, **kwargs: Any) -> list[VmSummary]:
            del kwargs
            raise RuntimeError("libvirt unavailable")

    registry = VmProviderRegistry(SimpleNamespace())
    registry._providers = {"libvirt": FailingProvider()}  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="libvirt unavailable"):
        registry.list_vms()


def test_azure_cloud_init_adds_default_host_key_to_agent_admin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_key = tmp_path / "id_ed25519"
    public_key = tmp_path / "id_ed25519.pub"
    private_key.write_text("private-key", encoding="utf-8")
    public_key.write_text("ssh-ed25519 AAAATEST jonathan@example", encoding="utf-8")
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_private_key_path",
        lambda: private_key,
    )
    vm_defaults = SimpleNamespace(
        azure=SimpleNamespace(ssh_public_key_path=None),
        guest_environment=lambda: {},
    )

    cloud_init = AzureVmProvider._render_cloud_init(
        vm_name="vm-azure-1",
        vm_user="agent",
        vm_defaults=vm_defaults,
    )

    assert "  - name: agent-admin" in cloud_init
    assert cloud_init.count("ssh-ed25519 AAAATEST jonathan@example") == 2


def test_azure_cloud_init_rejects_malicious_guest_environment_key() -> None:
    vm_defaults = SimpleNamespace(
        azure=SimpleNamespace(ssh_public_key_path=None),
        guest_environment=lambda: {"BAD; touch /tmp/vm-exec-pwned #": "1"},
    )

    with pytest.raises(ValueError, match="Invalid environment variable name"):
        AzureVmProvider._render_cloud_init(
            vm_name="vm-azure-1",
            vm_user="agent",
            vm_defaults=vm_defaults,
        )


def test_azure_cloud_init_accepts_valid_guest_environment_keys() -> None:
    vm_defaults = SimpleNamespace(
        azure=SimpleNamespace(ssh_public_key_path=None),
        guest_environment=lambda: {"VALID_1": "one", "_EMPTY": ""},
    )

    cloud_init = AzureVmProvider._render_cloud_init(
        vm_name="vm-azure-1",
        vm_user="agent",
        vm_defaults=vm_defaults,
    )

    assert "      VALID_1=one" in cloud_init
    assert "      _EMPTY=" in cloud_init
