from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.effects.providers.ado_pipeline_run import AdoPipelineRunEffectProvider
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.events.provider_models import AdoPipelineRunEffectDefinition


def _runtime_context(tmp_path: Path, *, env: dict[str, str] | None = None) -> EffectRuntimeContext:
    return EffectRuntimeContext(
        resolved_config=GatewayConfig(
            runtime_dir=tmp_path / "runtime",
            azure_devops_organization_url="https://dev.azure.com/example",
            azure_devops_project="Configured Project",
            azure_devops_pat_env="ADO_PAT",
        ),
        admin_service=SimpleNamespace(),
        env={"ADO_PAT": "secret-token"} if env is None else env,
    )


def _effect_definition(**overrides: object) -> AdoPipelineRunEffectDefinition:
    payload: dict[str, object] = {
        "id": "effect-ado-pipeline",
        "display_name": "ADO pipeline run",
        "type": "effect:ado:pipeline-run",
        "directory_path": "root/team",
        "pipeline_id": 42,
        "branch": "main",
        "variables": {"resourceBaseName": "demo7", "enableSeed": True},
        "template_parameters": {"configuration": "demo"},
        "poll_interval_seconds": 0,
    }
    payload.update(overrides)
    return AdoPipelineRunEffectDefinition.model_validate(payload)


def _install_mock_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    original_client = httpx.Client

    def _client_factory(*args: object, **kwargs: object) -> httpx.Client:
        kwargs = dict(kwargs)
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(
        "agent_sandbox.effects.providers.ado_pipeline_run.httpx.Client", _client_factory
    )


def test_ado_pipeline_run_queues_and_polls_to_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "id": 701,
                    "state": "inProgress",
                    "pipeline": {"id": 42, "name": "Deploy demo"},
                    "createdDate": "2026-05-26T10:00:00Z",
                    "_links": {"web": {"href": "https://dev.azure.test/run/701"}},
                },
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "id": 701,
                "state": "completed",
                "result": "succeeded",
                "pipeline": {"id": 42, "name": "Deploy demo"},
                "createdDate": "2026-05-26T10:00:00Z",
                "finishedDate": "2026-05-26T10:03:00Z",
                "_links": {"web": {"href": "https://dev.azure.test/run/701"}},
            },
            request=request,
        )

    _install_mock_client(monkeypatch, httpx.MockTransport(handler))
    provider = AdoPipelineRunEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(tmp_path))

    result = provider.execute()

    assert result == {
        "success": True,
        "queued": True,
        "completed": True,
        "run_id": "701",
        "run_url": "https://dev.azure.test/run/701",
        "pipeline_id": 42,
        "pipeline_name": "Deploy demo",
        "status": "completed",
        "result": "succeeded",
        "project": "Configured Project",
        "organization_url": "https://dev.azure.com/example",
        "started_at": "2026-05-26T10:00:00Z",
        "finished_at": "2026-05-26T10:03:00Z",
    }
    assert [request.method for request in requests] == ["POST", "GET"]
    assert requests[0].url.path == "/example/Configured Project/_apis/pipelines/42/runs"
    assert str(requests[0].url).startswith(
        "https://dev.azure.com/example/Configured%20Project/_apis/pipelines/42/runs"
    )
    assert requests[0].url.params["api-version"] == "7.1-preview.1"
    assert requests[0].headers["authorization"].startswith("Basic ")
    assert json.loads(requests[0].content) == {
        "resources": {"repositories": {"self": {"refName": "refs/heads/main"}}},
        "variables": {
            "resourceBaseName": {"value": "demo7"},
            "enableSeed": {"value": "true"},
        },
        "templateParameters": {"configuration": "demo"},
    }
    assert requests[1].url.path == "/example/Configured Project/_apis/pipelines/42/runs/701"


def test_ado_pipeline_run_raises_for_failed_checked_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": 702,
                "state": "completed",
                "result": "failed",
                "pipeline": {"id": 42, "name": "Deploy demo"},
            },
            request=request,
        )

    _install_mock_client(monkeypatch, httpx.MockTransport(handler))
    provider = AdoPipelineRunEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(tmp_path))

    with pytest.raises(RuntimeError, match="completed with result failed"):
        provider.execute()


def test_ado_pipeline_run_returns_failed_result_when_check_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": 703, "state": "completed", "result": "canceled"},
            request=request,
        )

    _install_mock_client(monkeypatch, httpx.MockTransport(handler))
    provider = AdoPipelineRunEffectProvider()
    provider.initialize(_effect_definition(check=False), _runtime_context(tmp_path))

    result = provider.execute()

    assert result["success"] is False
    assert result["completed"] is True
    assert result["result"] == "canceled"


def test_ado_pipeline_run_uses_directory_scoped_provider_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    class DirectoryConfigResolver:
        def __init__(self) -> None:
            self.paths: list[str] = []

        def resolve_for_directory(self, directory_path: str) -> GatewayConfig:
            self.paths.append(directory_path)
            return GatewayConfig(
                runtime_dir=tmp_path / "runtime",
                azure_devops_organization_url="https://dev.azure.com/directory",
                azure_devops_project="Directory Project",
                azure_devops_pat_env="ADO_PAT",
            )

    resolver = DirectoryConfigResolver()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"id": 704, "state": "completed", "result": "succeeded"},
            request=request,
        )

    _install_mock_client(monkeypatch, httpx.MockTransport(handler))
    provider = AdoPipelineRunEffectProvider()
    context = _runtime_context(tmp_path)
    context = EffectRuntimeContext(
        resolved_config=context.resolved_config,
        provider_config_resolver=resolver,
        admin_service=SimpleNamespace(),
        env={"ADO_PAT": "secret-token"},
    )
    provider.initialize(
        _effect_definition(directory_path="root/team/demo", project=None),
        context,
    )

    result = provider.execute()

    assert resolver.paths == ["root/team/demo"]
    assert result["project"] == "Directory Project"
    assert requests[0].url.path == "/directory/Directory Project/_apis/pipelines/42/runs"


def test_ado_pipeline_run_requires_configured_pat(tmp_path: Path) -> None:
    provider = AdoPipelineRunEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(tmp_path, env={}))

    with pytest.raises(RuntimeError, match="PAT is not configured"):
        provider.execute()
