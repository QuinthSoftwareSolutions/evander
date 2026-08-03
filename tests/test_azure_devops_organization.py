from __future__ import annotations

import pytest
from pydantic import ValidationError

from evander.azure_devops import resolve_azure_devops_organization_url
from evander.config import GatewayConfig
from evander.directory_config import DirectoryConfigStore


def test_gateway_config_migrates_supported_legacy_azure_devops_urls() -> None:
    config = GatewayConfig(
        azure_devops_organization_url="https://legacy-org.visualstudio.com/"
    )

    assert config.ado_organization == "legacy-org"
    assert config.azure_devops_organization_url == "https://dev.azure.com/legacy-org"


@pytest.mark.parametrize(
    "legacy_url",
    [
        "https://attacker.example/organization",
        "http://dev.azure.com/organization",
        "https://dev.azure.com/organization/extra",
        "https://user@dev.azure.com/organization",
    ],
)
def test_gateway_config_rejects_unsafe_legacy_azure_devops_urls(legacy_url: str) -> None:
    with pytest.raises(ValidationError):
        GatewayConfig(azure_devops_organization_url=legacy_url)


def test_directory_provider_config_migrates_legacy_organization_url_to_identifier() -> None:
    payload = DirectoryConfigStore._canonicalize_provider_domain_payload(
        {
            "azure_devops_organization_url": "https://dev.azure.com/directory-org",
            "azure_devops_project": "Directory Project",
        }
    )

    assert payload == {
        "ado_organization": "directory-org",
        "azure_devops_project": "Directory Project",
    }


def test_node_organization_is_constructed_at_the_trusted_azure_devops_origin() -> None:
    assert resolve_azure_devops_organization_url(
        ado_organization="workflow-org",
        legacy_organization_url="https://dev.azure.com/directory-org",
        node_type="effect:pull-request:update",
    ) == "https://dev.azure.com/workflow-org"


def test_legacy_workflow_organization_url_is_compatible_only_for_azure_devops() -> None:
    assert resolve_azure_devops_organization_url(
        ado_organization="https://dev.azure.com/legacy-workflow-org",
        legacy_organization_url=None,
        node_type="query:pull-request:get",
    ) == "https://dev.azure.com/legacy-workflow-org"

    with pytest.raises(ValueError, match=r"dev\.azure\.com or \*\.visualstudio\.com"):
        resolve_azure_devops_organization_url(
            ado_organization="https://attacker.example/organization",
            legacy_organization_url=None,
            node_type="query:pull-request:get",
        )
