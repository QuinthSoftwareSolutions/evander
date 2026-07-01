from __future__ import annotations

from pathlib import Path

from evander.config import GatewayConfig
from evander.effects.registry import EffectProviderFactory
from evander.events.provider_models import IssueUpsertAdoProxyEffectDefinition
from evander.models import LocalIssueRecord
from evander.storage import JsonStore


class _ResolverStub:
    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir

    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        del directory_path
        return GatewayConfig(
            runtime_dir=self.runtime_dir,
            azure_devops_organization_url="https://dev.azure.com/example",
            azure_devops_project="Project",
        )


def test_issue_upsert_ado_proxy_effect_accepts_batch_items(tmp_path: Path) -> None:
    effect = IssueUpsertAdoProxyEffectDefinition(
        id="upsert-ado-proxy",
        display_name="Upsert ADO proxy",
        type="effect:issue:upsert-ado-proxy",
        directory_path="root/team-a",
        repo_source="/repo",
        items=[
            {
                "id": "101",
                "title": "Fix picker",
                "state": "Committed",
                "fields": {"System.ChangedDate": "2026-05-22T10:00:00Z"},
            }
        ],
    )
    provider = EffectProviderFactory(provider_config_resolver=_ResolverStub(tmp_path)).build(
        effect,
        directory_path="root/team-a",
    )

    output = provider.execute()

    assert output["success"] is True
    assert output["created"] == 1
    assert output["updated"] == 0
    assert output["count"] == 1
    assert output["ticket"]["repo_source"] == "/repo"
    assert (tmp_path / "ado-work-items" / "work-item-101.yaml").exists()
    stored = JsonStore(tmp_path / "local-issues.json", LocalIssueRecord).read_all()
    assert len(stored) == 1


def test_issue_upsert_ado_proxy_effect_accepts_query_result_shape(tmp_path: Path) -> None:
    effect = IssueUpsertAdoProxyEffectDefinition(
        id="upsert-ado-proxy",
        display_name="Upsert ADO proxy",
        type="effect:issue:upsert-ado-proxy",
        item={
            "items": [
                {
                    "id": "101",
                    "fields": {
                        "System.Title": "From query",
                        "System.ChangedDate": "2026-05-22T10:00:00Z",
                    },
                }
            ]
        },
    )
    provider = EffectProviderFactory(provider_config_resolver=_ResolverStub(tmp_path)).build(
        effect,
        directory_path="root/team-a",
    )

    output = provider.execute()

    assert output["ticket"]["title"] == "From query"
    assert output["ticket"]["directory_path"] == "root/team-a"
