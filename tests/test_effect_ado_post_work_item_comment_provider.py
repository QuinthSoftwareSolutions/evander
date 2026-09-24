from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
import time
from types import SimpleNamespace
from typing import Any

import pytest

from evander.config import GatewayConfig
from evander.effects.providers import ado_post_work_item_comment as provider_module
from evander.effects.providers.ado_post_work_item_comment import (
    AdoPostWorkItemCommentEffectProvider,
)
from evander.effects.providers.base import EffectRuntimeContext


class _IssueServiceStub:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.post_calls: list[dict[str, Any]] = []

    def post_remote_comment(self, **kwargs: Any) -> dict[str, Any]:
        self.post_calls.append(kwargs)
        return dict(self.result)


def _provider(
    tmp_path: Path,
    *,
    provider_config_resolver: Any | None = None,
    **effect_fields: Any,
) -> AdoPostWorkItemCommentEffectProvider:
    class _Resolver:
        def resolve_for_directory(self, _directory_path: str) -> SimpleNamespace:
            return SimpleNamespace(
                azure_devops_organization_url="https://dev.azure.com/example",
            )

    provider = AdoPostWorkItemCommentEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-ado-post-work-item-comment",
            type="effect:ado:post-work-item-comment",
            **effect_fields,
        ),
        runtime_context=EffectRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
            provider_config_resolver=provider_config_resolver or _Resolver(),
            env={},
        ),
    )
    return provider


def test_ado_post_work_item_comment_posts_comment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _IssueServiceStub(
        {
            "provider": "azure_devops",
            "remote_issue_id": "123",
            "comment": {
                "id": "456",
                "url": "https://dev.azure.com/example/_workitems/edit/123#comment-456",
            },
        }
    )
    monkeypatch.setattr(provider_module, "IssueService", lambda resolver: service)
    provider = _provider(
        tmp_path,
        directory_path="root/example/evander",
        project="Example Industry App",
        work_item_id="123",
        comment="Please assign this work item to the owner.",
    )

    output = provider.execute()

    assert output == {
        "success": True,
        "status": "posted",
        "provider": "azure_devops",
        "work_item_id": "123",
        "comment_id": "456",
        "comment_url": "https://dev.azure.com/example/_workitems/edit/123#comment-456",
        "comment": {
            "id": "456",
            "url": "https://dev.azure.com/example/_workitems/edit/123#comment-456",
        },
        "resolved_mentions": [],
        "idempotency_key": None,
        "correlation_marker": None,
        "message": "Posted comment to Azure DevOps work item 123",
    }
    assert service.post_calls == [
        {
            "provider": "azure_devops",
            "remote_issue_id": "123",
            "directory_path": "root/example/evander",
            "project": "Example Industry App",
            "comment": "Please assign this work item to the owner.",
            "mentions": None,
        }
    ]


def test_ado_post_work_item_comment_passes_normalized_mentions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _IssueServiceStub(
        {
            "provider": "azure_devops",
            "remote_issue_id": "123",
            "comment": {"id": "456"},
            "resolved_mentions": [
                {
                    "input": "owner@example.com",
                    "id": "identity-1",
                    "display_name": "Owner",
                }
            ],
        }
    )
    monkeypatch.setattr(provider_module, "IssueService", lambda resolver: service)
    provider = _provider(
        tmp_path,
        directory_path="root/example/evander",
        project="Example Industry App",
        work_item_id="123",
        comment="Could you clarify the acceptance criterion?",
        mentions=[" owner@example.com ", "OWNER@example.com"],
    )

    output = provider.execute()

    assert service.post_calls[0]["mentions"] == ["owner@example.com"]
    assert output["resolved_mentions"] == [
        {
            "input": "owner@example.com",
            "id": "identity-1",
            "display_name": "Owner",
        }
    ]


class _RetrySafeIssueServiceStub:
    def __init__(
        self,
        *,
        fail_after_post_once: bool = False,
        resolved_mentions: list[dict[str, str]] | None = None,
    ) -> None:
        self.fail_after_post_once = fail_after_post_once
        self.resolved_mentions = resolved_mentions or []
        self.read_calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []
        self.resolve_mention_calls: list[dict[str, Any]] = []
        self.comments: list[dict[str, Any]] = []

    def read_remote_issue(self, **kwargs: Any) -> dict[str, Any]:
        self.read_calls.append(kwargs)
        return {"comments": [dict(comment) for comment in self.comments]}

    def post_remote_comment(self, **kwargs: Any) -> dict[str, Any]:
        self.post_calls.append(kwargs)
        comment_id = str(len(self.comments) + 1)
        posted = {
            "id": comment_id,
            "text": f"<p>{kwargs['comment']}</p>\n{kwargs['correlation_marker']}",
            "url": f"https://dev.azure.com/example/comment/{comment_id}",
        }
        self.comments.insert(0, posted)
        if self.fail_after_post_once:
            self.fail_after_post_once = False
            raise TimeoutError("response was lost after Azure DevOps accepted the comment")
        return {
            "provider": "azure_devops",
            "remote_issue_id": kwargs["remote_issue_id"],
            "comment": posted,
            "resolved_mentions": list(self.resolved_mentions),
        }

    def resolve_ado_comment_mentions(self, **kwargs: Any) -> list[dict[str, str]]:
        self.resolve_mention_calls.append(kwargs)
        return list(self.resolved_mentions)


def test_ado_post_work_item_comment_idempotency_reuses_local_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _RetrySafeIssueServiceStub()
    monkeypatch.setattr(provider_module, "IssueService", lambda resolver: service)
    provider = _provider(
        tmp_path,
        directory_path="root/example/evander",
        project="Example Industry App",
        work_item_id="123",
        comment="What result should this produce?",
        mentions=["owner@example.com"],
        idempotency_key="clarification:123:round-1",
    )

    first = provider.execute()
    second = provider.execute()

    assert first["status"] == "posted"
    assert second["status"] == "deduplicated"
    assert second["comment_id"] == "1"
    assert second["correlation_marker"] == first["correlation_marker"]
    assert len(service.read_calls) == 1
    assert len(service.post_calls) == 1
    assert service.post_calls[0]["mentions"] == ["owner@example.com"]
    assert service.post_calls[0]["correlation_marker"] == first["correlation_marker"]


def test_ado_post_work_item_comment_recovers_ambiguous_remote_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_mentions = [
        {
            "input": "owner@example.com",
            "id": "identity-1",
            "display_name": "Owner",
        }
    ]
    service = _RetrySafeIssueServiceStub(
        fail_after_post_once=True,
        resolved_mentions=resolved_mentions,
    )
    monkeypatch.setattr(provider_module, "IssueService", lambda resolver: service)
    provider = _provider(
        tmp_path,
        directory_path="root/example/evander",
        project="Example Industry App",
        work_item_id="123",
        comment="Which tolerance applies?",
        mentions=["owner@example.com"],
        idempotency_key="clarification:123:round-2",
    )

    with pytest.raises(TimeoutError, match="response was lost"):
        provider.execute()
    recovered = provider.execute()

    assert recovered["status"] == "deduplicated"
    assert recovered["comment_id"] == "1"
    assert recovered["resolved_mentions"] == resolved_mentions
    assert len(service.read_calls) == 2
    assert len(service.post_calls) == 1
    assert service.resolve_mention_calls == [
        {
            "directory_path": "root/example/evander",
            "mentions": ["owner@example.com"],
        }
    ]


def test_ado_post_work_item_comment_rejects_idempotency_payload_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _RetrySafeIssueServiceStub()
    monkeypatch.setattr(provider_module, "IssueService", lambda resolver: service)
    provider = _provider(
        tmp_path,
        directory_path="root/example/evander",
        project="Example Industry App",
        work_item_id="123",
        comment="First question",
        idempotency_key="clarification:123:round-3",
    )
    provider.execute()

    with pytest.raises(ValueError, match="different comment or mention set"):
        provider.execute(resolved_config={"comment": "Different question"})

    assert len(service.post_calls) == 1


def test_ado_post_work_item_comment_rejects_remote_marker_payload_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _RetrySafeIssueServiceStub(fail_after_post_once=True)
    monkeypatch.setattr(provider_module, "IssueService", lambda resolver: service)
    provider = _provider(
        tmp_path,
        directory_path="root/example/evander",
        project="Example Industry App",
        work_item_id="123",
        comment="Original question",
        idempotency_key="clarification:123:round-conflict",
    )
    with pytest.raises(TimeoutError, match="response was lost"):
        provider.execute()

    with pytest.raises(ValueError, match="different comment or mention set"):
        provider.execute(resolved_config={"comment": "Different question"})

    assert len(service.post_calls) == 1


def test_ado_post_work_item_comment_idempotency_is_process_safe_between_instances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = Barrier(3)

    class _SlowIssueServiceStub(_RetrySafeIssueServiceStub):
        def post_remote_comment(self, **kwargs: Any) -> dict[str, Any]:
            time.sleep(0.05)
            return super().post_remote_comment(**kwargs)

    service = _SlowIssueServiceStub()
    monkeypatch.setattr(provider_module, "IssueService", lambda resolver: service)
    providers = [
        _provider(
            tmp_path,
            directory_path="root/example/evander",
            project="Example Industry App",
            work_item_id="123",
            comment="Is this expected to be backward compatible?",
            idempotency_key="clarification:123:round-4",
        )
        for _ in range(2)
    ]

    def post(provider: AdoPostWorkItemCommentEffectProvider) -> dict[str, Any]:
        start.wait()
        return provider.execute()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(post, provider) for provider in providers]
        start.wait()
        outputs = [future.result(timeout=2) for future in futures]

    assert sorted(output["status"] for output in outputs) == ["deduplicated", "posted"]
    assert len(service.read_calls) == 1
    assert len(service.post_calls) == 1


def test_ado_post_work_item_comment_idempotency_is_scoped_to_resolved_ado_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _RetrySafeIssueServiceStub()
    monkeypatch.setattr(provider_module, "IssueService", lambda resolver: service)

    class _SharedTargetResolver:
        def resolve_for_directory(self, _directory_path: str) -> SimpleNamespace:
            return SimpleNamespace(
                azure_devops_organization_url="https://DEV.azure.com/Example/",
            )

    providers = [
        _provider(
            tmp_path,
            provider_config_resolver=_SharedTargetResolver(),
            directory_path=directory_path,
            project="Example Industry App",
            work_item_id="123",
            comment="Which environment should validate this?",
            idempotency_key="clarification:123:round-5",
        )
        for directory_path in ("root/team-a", "root/team-b")
    ]

    first = providers[0].execute()
    second = providers[1].execute()

    assert first["status"] == "posted"
    assert second["status"] == "deduplicated"
    assert len(service.post_calls) == 1
