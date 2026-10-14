from __future__ import annotations
from pathlib import Path
from evander.pr_feedback import AzureDevOpsFeedbackAdapter
from evander.triggers.providers.pull_request_new_feedback import (
    PullRequestNewFeedbackTriggerProvider,
)

from test_trigger_conformance_suite import _build_runtime_context, _trigger_adapter


def test_pr_feedback_trigger_ado_adapter_uses_trigger_org_and_project(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    provider = PullRequestNewFeedbackTriggerProvider()
    provider.initialize(
        _trigger_adapter().validate_python(
            {
                "id": "pr-ado",
                "display_name": "PR ADO",
                "type": "trigger:pull-request:new-feedback",
                "provider": "ado",
                "organization_url": "https://dev.azure.com/example-org",
                "project": "Example Project",
            }
        ),
        _build_runtime_context(
            runtime_dir,
            env={"AZURE_DEVOPS_PR_PAT": "secret-pat"},
        ),
    )

    adapter = provider._build_adapter("ado")

    assert isinstance(adapter, AzureDevOpsFeedbackAdapter)
    assert adapter.organization_url == "https://dev.azure.com/example-org"
    assert adapter.project == "Example Project"
    assert adapter.pat == "secret-pat"
