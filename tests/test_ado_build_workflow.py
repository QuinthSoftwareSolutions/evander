from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from evander.config import GatewayConfig
from evander.effects.providers.ado_build_wait import AdoBuildWaitEffectProvider
from evander.effects.providers.base import EffectProviderExecutionError, EffectRuntimeContext
from evander.events.provider_models import AdoBuildsQueryDefinition, AdoBuildWaitEffectDefinition
from evander.queries.providers.ado_builds import AdoBuildsQueryProvider
from evander.queries.providers.base import QueryRuntimeContext


def context(tmp_path, kind=QueryRuntimeContext):
    config = GatewayConfig(
        runtime_dir=tmp_path,
        azure_devops_organization_url="https://dev.azure.com/example",
        azure_devops_project="Project",
        azure_devops_pat_env="BUILD_PAT",
    )
    return kind(
        provider_config_resolver=SimpleNamespace(resolve_for_directory=lambda path: config),
        env={"BUILD_PAT": "do-not-print-me"},
        metadata={"directory_path": "root/app"},
    )


def install_transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        "evander.ado_builds.httpx.Client",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)),
    )


def build(
    run_id,
    *,
    branch="refs/pull/17/merge",
    sha="merge-current",
    status="completed",
    result="succeeded",
):
    return {
        "id": run_id,
        "buildNumber": f"build-{run_id}",
        "queueTime": f"2026-09-17T10:{run_id:02}:00Z",
        "sourceBranch": branch,
        "sourceVersion": sha,
        "repository": {"id": "repository"},
        "definition": {"id": 44},
        "status": status,
        "result": result,
    }


def query(tmp_path, **overrides):
    node = AdoBuildsQueryDefinition(
        id="q",
        display_name="Builds",
        type="query:ado:builds",
        directory_path="root/app",
        pipeline_id=44,
        repository_id="repository",
        filters=[
            {"branch": "refs/heads/feature", "source_version": "source-current"},
            {"branch": "refs/pull/17/merge", "source_version": "merge-current"},
        ],
        limit=1,
    )
    provider = AdoBuildsQueryProvider()
    provider.initialize(node, context(tmp_path))
    return provider.execute(resolved_config=overrides)


@pytest.mark.parametrize(
    "status,result",
    [
        ("inProgress", None),
        ("notStarted", None),
        ("completed", "failed"),
        ("completed", "canceled"),
        ("completed", "partiallySucceeded"),
        ("completed", "succeeded"),
    ],
)
def test_selects_latest_attempt_without_success_filter(monkeypatch, tmp_path, status, result):
    requests = []

    def handler(request):
        requests.append(request)
        assert "statusFilter" not in request.url.params
        assert "resultFilter" not in request.url.params
        assert request.url.params["definitions"] == "44"
        assert request.url.params["repositoryId"] == "repository"
        branch = request.url.params["branchName"]
        runs = [build(9, status=status, result=result), build(8), build(10, sha="old-commit")]
        return httpx.Response(200, json={"value": runs if branch.startswith("refs/pull/") else []})

    install_transport(monkeypatch, handler)
    output = query(tmp_path)
    assert output["count"] == 1
    assert output["builds"][0]["run_id"] == "9"
    assert output["builds"][0]["status"] == status.lower()
    assert output["builds"][0]["success"] == (result == "succeeded")
    assert len(requests) == 2
    assert "do-not-print-me" not in str(output)


def test_finds_current_revision_across_pages_and_both_ref_types(monkeypatch, tmp_path):
    def handler(request):
        if request.url.params["branchName"].startswith("refs/pull/"):
            return httpx.Response(200, json={"value": [build(8)]})
        if "continuationToken" not in request.url.params:
            return httpx.Response(
                200,
                json={"value": [build(10, branch="refs/heads/feature", sha="old")]},
                headers={"x-ms-continuationtoken": "page-two"},
            )
        return httpx.Response(
            200, json={"value": [build(9, branch="refs/heads/feature", sha="source-current")]}
        )

    install_transport(monkeypatch, handler)
    assert query(tmp_path)["builds"][0]["run_id"] == "9"


def test_no_build_for_revision_is_an_explicit_empty_result(monkeypatch, tmp_path):
    install_transport(
        monkeypatch, lambda request: httpx.Response(200, json={"value": [build(3, sha="old")]})
    )
    assert query(tmp_path) == {"count": 0, "builds": []}


def test_filters_support_non_pr_refs_and_return_multiple_builds(monkeypatch, tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.params["branchName"] == "refs/tags/release-v2"
        return httpx.Response(
            200,
            json={
                "value": [
                    build(9, branch="refs/tags/release-v2", sha="abc"),
                    build(8, branch="refs/tags/release-v2", sha="def"),
                ]
            },
        )

    install_transport(monkeypatch, handler)
    result = query(tmp_path, filters=[{"branch": "refs/tags/release-v2"}], limit=10)
    assert [build["run_id"] for build in result["builds"]] == ["9", "8"]
    assert result["count"] == 2
    assert len(requests) == 1


@pytest.mark.parametrize("version", [None, "", " "])
def test_explicit_unknown_version_is_not_treated_as_any_version(monkeypatch, tmp_path, version):
    def handler(request):
        pytest.fail("Disabled filters must not send a request")

    install_transport(monkeypatch, handler)
    assert query(
        tmp_path, filters=[{"branch": "refs/tags/release-v2", "source_version": version}]
    ) == {"count": 0, "builds": []}


@pytest.mark.parametrize("version", [False, 0, 123, []])
def test_invalid_version_type_is_not_silently_disabled(monkeypatch, tmp_path, version):
    install_transport(
        monkeypatch, lambda request: pytest.fail("Invalid filter must not send a request")
    )
    with pytest.raises(ValueError, match="source_version"):
        query(tmp_path, filters=[{"branch": "refs/tags/release-v2", "source_version": version}])


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_lookup_errors_do_not_copy_upstream_body_or_credentials(monkeypatch, tmp_path, status):
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(status, text="private upstream content do-not-print-me"),
    )
    with pytest.raises(RuntimeError) as error:
        query(tmp_path)
    assert str(status) in str(error.value)
    assert "private" not in str(error.value)
    assert "do-not-print-me" not in str(error.value)


def wait(tmp_path, **kwargs):
    provider = AdoBuildWaitEffectProvider()
    provider.initialize(
        AdoBuildWaitEffectDefinition(
            id="w",
            display_name="Wait",
            type="effect:ado:build-wait",
            directory_path="root/app",
            run_id=9,
        ),
        context(tmp_path, EffectRuntimeContext),
    )
    return provider.execute(resolved_config=kwargs)


def test_waits_for_only_the_selected_build(monkeypatch, tmp_path):
    statuses = iter([("notStarted", None), ("inProgress", None), ("completed", "succeeded")])
    sleeps = []

    def handler(request):
        assert request.method == "GET"
        assert request.url.path.endswith("/build/builds/9")
        status, result = next(statuses)
        return httpx.Response(200, json=build(9, status=status, result=result))

    install_transport(monkeypatch, handler)
    monkeypatch.setattr("evander.effects.providers.ado_build_wait.time.sleep", sleeps.append)
    assert wait(tmp_path)["success"]
    assert sleeps == [10, 10]


@pytest.mark.parametrize("result", ["failed", "canceled", "partiallySucceeded"])
def test_wait_failure_includes_run_link(monkeypatch, tmp_path, result):
    install_transport(
        monkeypatch, lambda request: httpx.Response(200, json=build(9, result=result))
    )
    with pytest.raises(EffectProviderExecutionError) as error:
        wait(tmp_path)
    assert error.value.outputs["run_url"].endswith("buildId=9")
    assert error.value.outputs["result"] == result.lower()


def test_wait_timeout_is_bounded(monkeypatch, tmp_path):
    clock = iter([0, 3601])
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, json=build(9, status="inProgress", result=None)),
    )
    monkeypatch.setattr(
        "evander.effects.providers.ado_build_wait.time.monotonic", lambda: next(clock)
    )
    with pytest.raises(EffectProviderExecutionError, match="Timed out"):
        wait(tmp_path)


def test_directory_cannot_escape_workflow_scope(tmp_path):
    with pytest.raises(ValueError, match="outside workflow directory"):
        query(tmp_path, directory_path="root/other")


@pytest.mark.parametrize("field", ["timeout_seconds", "poll_interval_seconds"])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_wait_rejects_invalid_time_bounds(tmp_path, field, value):
    with pytest.raises(ValueError, match="positive finite"):
        wait(tmp_path, **{field: value})
