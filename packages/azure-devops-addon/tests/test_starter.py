from datetime import UTC, datetime, timedelta
import json

import pytest

from config_helpers import write_runtime_config
from evander.addons import AddonLoaderConfig
from evander.admin.composition import build_admin_runtime
from evander.config import OrchestrationCoreConfig
from evander.daemons.effect_orchestrator import _build_execution_graph
from evander.models import SessionManifest
from evander.workflow.graph import compute_downstream_depth
from evander.workflow.runtime_config import load_trigger_orchestrator_worker_global_config


def values(**overrides):
    return (
        dict(
            name="selected-issue",
            session_id="coder",
            managed_repo_id="mr_demo",
            work_item_id="42",
            base_branch="main",
            pipeline_id="7",
            min_reviewers="1",
            max_repairs="3",
        )
        | overrides
    )


@pytest.fixture
def admin(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_ADO", "fixture-work-items")
    monkeypatch.setenv("TEST_PR", "fixture-pull-requests")
    directory = write_runtime_config(
        tmp_path,
        ado_organization="example",
        azure_devops_project="Project",
        azure_devops_pat_env="TEST_ADO",
        azure_devops_pr_pat_env="TEST_PR",
        addon_loader=AddonLoaderConfig(modules=["evander_azure_devops_addon"], package_dirs=[]),
    )
    service = build_admin_runtime(directory)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="coder",
            vm_id="vm-coder",
            allowed_tools=[],
            metadata={
                "managed_git_access_grants": {
                    "mr_demo": {
                        "managed_repo_id": "mr_demo",
                        "access_mode": "write",
                        "lifetime": "session",
                    }
                }
            },
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    record = {
        "managed_repo_id": "mr_demo",
        "display_name": "Demo repository",
        "provider": "azure_devops",
        "canonical_repository_url": "https://dev.azure.com/example/Project/_git/demo",
        "bindings_by_kind": {"session": ["coder"]},
    }
    monkeypatch.setattr(service.managed_git_admin, "get_repository", lambda *a, **kw: record)
    monkeypatch.setattr(service.managed_git_admin, "list_repositories", lambda **kw: [record])
    return service, record


def test_starter_previews_and_installs_editable_workflow_without_expanding_permissions(admin):
    service, _ = admin
    catalog = service.workflow_starter_admin.catalog("root")
    assert {"value": "mr_demo", "label": "Demo repository"} in catalog["repositories"]
    assert any(item["id"] == "azure-devops/issue-to-merge" for item in catalog["starters"])
    preview = service.workflow_starter_admin.preview(
        "azure-devops/issue-to-merge", "root", values()
    )
    assert preview["ready"]
    assert not (
        service.project_dir / "config/workflow-packs/starter-selected-issue.v1.json"
    ).exists()
    receipt = service.workflow_starter_admin.activate(
        "azure-devops/issue-to-merge",
        "root",
        values(),
        preview_revision=preview["preview_revision"],
    )
    document = service.workflow_config_admin.read_workflow_editor_document(
        workflow_pack=receipt["workflow_pack"]
    )
    assert document.graphs[0].directory_path == "root"
    serialized = json.dumps(preview["pack"])
    assert "fixture-work-items" not in serialized and "fixture-pull-requests" not in serialized
    assert service.runtime.session_repository.get_session("coder").allowed_tools == []
    nodes = {node["node"]["id"]: node["node"] for node in preview["pack"]["ir"]["nodes"]}
    assert (
        nodes["starter-selected-issue-ours"]["config"]["when"]["all"][1]["equals"]
        == "agents/coder/selected-issue"
    )
    assert nodes["starter-selected-issue-pr"]["config"]["base_branch"] == "main"
    assert nodes["starter-selected-issue-claim"]["config"]["max_repairs"] == 3
    assert nodes["starter-selected-issue-merge"]["type"] == "effect:ado:merge-approved"
    worker_config = load_trigger_orchestrator_worker_global_config(
        service.project_dir, base_config=service.config
    )
    graph = _build_execution_graph(worker_config)
    assert worker_config.max_graph_depth == OrchestrationCoreConfig.model_fields["max_graph_depth"].default
    for edge in worker_config.edges:
        assert compute_downstream_depth(edge.target_id, graph.downstream_index) <= worker_config.max_graph_depth
    assert {
        graph.node_graph_metadata_by_id[node_id].graph_id
        for node_id in nodes
    } == {"starter-selected-issue"}


def test_starter_refuses_a_repository_not_cloned_into_the_selected_agent(admin):
    service, record = admin
    record["bindings_by_kind"]["session"] = ["another-agent"]
    with pytest.raises(ValueError, match="Clone this managed repository"):
        service.workflow_starter_admin.preview("azure-devops/issue-to-merge", "root", values())


def test_starter_rejects_read_only_repository_access_without_granting_write(admin):
    service, _ = admin
    session = service.runtime.session_repository.get_session("coder")
    metadata = {
        "managed_git_access_grants": {
            "mr_demo": {"managed_repo_id": "mr_demo", "access_mode": "read-only"}
        }
    }
    service.runtime.session_repository.upsert_session(
        session.model_copy(update={"metadata": metadata})
    )
    with pytest.raises(ValueError, match="existing write access"):
        service.workflow_starter_admin.preview("azure-devops/issue-to-merge", "root", values())
    assert service.runtime.session_repository.get_session("coder").metadata == metadata
