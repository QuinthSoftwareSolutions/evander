"""Observable edge cases preserved by the internal helper refactors."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path

import pytest
from pydantic import BaseModel, Field, ValidationError
from starlette.requests import Request

from evander.config import TriggerOrchestratorWorkerGlobalConfig
from evander.models import (
    AdoBacklogItemType,
    AdoBacklogQueryRequest,
    AdoCurrentIterationBacklogRequest,
    AdoDailyStatusReportRequest,
)
from evander.workflow.contracts import WorkflowNodeEnvelope


@pytest.mark.parametrize(
    "module",
    [
        "evander.execution.container_worker_backend",
        "evander.admin.services.vm_agent.common_helpers",
    ],
)
def test_log_tail_pages_filter_before_counting_and_apply_offsets(module: str) -> None:
    page = import_module(module)._log_page_from_end(
        "INFO ready\r\nWARN first\r\ninfo running\r\nwarn second\r\nWARN last",
        limit=1,
        offset=1,
        query="  wArN  ",
    )
    assert page == {
        "text": "warn second\n",
        "limit": 1,
        "offset": 1,
        "returned_lines": 1,
        "total_lines": 3,
        "start_line": 2,
        "end_line": 2,
        "has_more_before": True,
        "query": "wArN",
    }
    tail = import_module(module)._log_page_from_end("first\nlast", limit=0, offset=-1)
    assert tail["text"] == "last\n"
    assert (tail["limit"], tail["offset"], tail["start_line"]) == (1, 0, 2)
    empty = import_module(module)._log_page_from_end("only", limit=5, offset=8)
    assert (empty["text"], empty["returned_lines"], empty["start_line"], empty["end_line"]) == (
        "",
        0,
        0,
        0,
    )
    assert empty["total_lines"] == 1
    assert empty["has_more_before"] is False


class _DatedPayload(BaseModel):
    occurred_at: datetime


@pytest.mark.parametrize("module", ["agents", "capacity", "skills", "vms_issues"])
def test_admin_json_conversion_preserves_nested_shapes_and_time_offsets(module: str) -> None:
    convert = import_module(f"evander.admin.web.{module}")._jsonable
    opaque = object()
    result = convert(
        {
            12: (_DatedPayload(occurred_at=datetime(2026, 1, 2, tzinfo=UTC)),),
            "local": datetime(2026, 1, 2, 3, tzinfo=timezone(timedelta(hours=2))),
            "day": date(2026, 1, 2),
            "opaque": opaque,
        }
    )
    assert result == {
        "12": [{"occurred_at": "2026-01-02T00:00:00Z"}],
        "local": "2026-01-02T03:00:00+02:00",
        "day": "2026-01-02",
        "opaque": opaque,
    }
    assert result["opaque"] is opaque


def _request(body: bytes) -> Request:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "method": "POST", "headers": []}, receive)


@pytest.mark.parametrize(
    "module,name",
    [
        ("agents", "_json_body"),
        ("skills", "_json_body"),
        ("vms_issues", "_json_body"),
        ("operator_steering", "_json_body"),
        ("addons", "_json_body"),
        ("workers_config", "_json_body"),
        ("workflow", "json_body"),
    ],
)
def test_admin_request_decoding_preserves_errors_and_exception_chaining(
    module: str, name: str
) -> None:
    decode = getattr(import_module(f"evander.admin.web.{module}"), name)
    for body in (b"null", b"[]", b"42", b'"text"'):
        with pytest.raises(ValueError, match="^Request body must be a JSON object$"):
            asyncio.run(decode(_request(body)))
    with pytest.raises(ValueError, match="^Request body must be valid JSON:") as error:
        asyncio.run(decode(_request(b'{"broken":')))
    assert isinstance(error.value.__cause__, json.JSONDecodeError)
    assert str(error.value).endswith(error.value.__cause__.msg)
    with pytest.raises(UnicodeDecodeError):
        asyncio.run(decode(_request(b"\xff")))


@pytest.mark.parametrize(
    "module", ["for_gate", "while_gate", "await_agent", "debounce_gate", "buffer_gate"]
)
def test_gate_state_paths_keep_defaults_for_missing_or_non_dict_namespaces(module: str) -> None:
    resolve = import_module(f"evander.gates.providers.{module}")._provider_state_path
    default = Path("runtime/fallback.json")
    for state in (None, {}, {"gate": None}, {"gate": []}, {"gate": {"path": "  "}}):
        assert resolve(state, namespace=("gate", "path"), default=default) is default
    assert resolve(
        {"gate": {"path": " relative/state.json "}},
        namespace=("gate", "path"),
        default=default,
    ) == Path("relative/state.json")
    assert resolve({"gate": {"path": 0}}, namespace=("gate", "path"), default=default) == Path("0")


@pytest.mark.parametrize(
    "module,name",
    [
        ("evander.cli_session_support", "_load_optional_env_file"),
        ("evander.migrations", "_load_optional_env_file"),
        ("evander.setup.engine", "_parse_env_file"),
    ],
)
def test_optional_env_files_keep_literal_values_and_last_duplicate(
    module: str, name: str, tmp_path: Path
) -> None:
    load = getattr(import_module(module), name)
    path = tmp_path / "synthetic.env"
    assert load(path) == {}
    path.write_text(
        "# ignored\ninvalid\n = empty key\n"
        " FIRST = old\nFIRST='new'\nQUOTED=\"a=b\"\n"
        "LITERAL=${FIRST} # preserved\nEMPTY=\n",
        encoding="utf-8",
    )
    assert load(path) == {
        "FIRST": "new",
        "QUOTED": "a=b",
        "LITERAL": "${FIRST} # preserved",
        "EMPTY": "",
    }
    if module == "evander.setup.engine":
        assert load(tmp_path) == {}
    else:
        with pytest.raises(IsADirectoryError):
            load(tmp_path)


@pytest.mark.parametrize(
    "module", ["evander.pr_feedback.providers", "evander.integration_services.pull_requests"]
)
@pytest.mark.parametrize(
    "repo,expected",
    [
        ("  Repo%20One  ", "Repo One"),
        ("https://dev.azure.com/org/project/_git/Repo%20One/extra", "Repo One"),
        ("https://dev.azure.com/org/project/_apis/git/repositories/repo-id/items", "repo-id"),
        ("git@ssh.dev.azure.com:v3/org/project/Repo", "Repo"),
        ("org/project/Repo/", ""),
        ("org/Repo", "org/Repo"),
    ],
)
def test_pr_repository_identifiers_preserve_url_decoding_and_slash_rules(
    module: str, repo: str, expected: str
) -> None:
    assert import_module(module)._normalize_azure_devops_repo_identifier(repo) == expected


class _ProviderNode(BaseModel):
    id: str
    display_name: str
    type: str
    outputs: dict[str, dict[str, object]] | None
    size: int = Field(ge=1)


@pytest.mark.parametrize(
    "module",
    [
        "evander.events.models",
        "evander.triggers.providers.default_schema",
        "evander.gates.providers.default_schema",
        "evander.effects.providers.default_schema",
        "evander.queries.providers.default_schema",
    ],
)
def test_node_materialization_keeps_config_precedence_and_provider_validation(module: str) -> None:
    materialize = import_module(module)._materialize_with_model
    node = WorkflowNodeEnvelope(
        id="envelope",
        display_name="Example",
        type="query:example",
        outputs={"original": {"type": "object"}},
        config={"id": "provider-id", "outputs": None, "size": "3"},
    )
    result = materialize(_ProviderNode, node)
    assert result.model_dump() == {
        "id": "provider-id",
        "display_name": "Example",
        "type": "query:example",
        "outputs": None,
        "size": 3,
    }
    assert node.id == "envelope"
    assert node.config["size"] == "3"
    node.config["size"] = "invalid"
    with pytest.raises(ValidationError) as error:
        materialize(_ProviderNode, node)
    assert [(item["loc"], item["type"]) for item in error.value.errors()] == [
        (("size",), "int_parsing")
    ]


@pytest.mark.parametrize("module", ["trigger_orchestrator", "effect_orchestrator"])
def test_provider_reload_signature_keeps_existing_hash_and_ignores_poll_interval(
    module: str,
) -> None:
    signature = import_module(f"evander.daemons.{module}")._provider_loader_signature
    config = TriggerOrchestratorWorkerGlobalConfig(
        runtime_dir=Path("runtime"),
        orchestration_store_path=Path("runtime/orchestration.db"),
        lock_path=Path("runtime/trigger.lock"),
    )
    expected = "8ad9014351cf882e836be071a44d0d66ae58187737dadb6b9f2b1755f927b3cc"
    assert signature(config) == expected
    assert signature(config.model_copy(update={"poll_interval_seconds": 9.0})) == expected
    for name, value in {
        "orchestration_store_path": Path("runtime/other.db"),
        "lock_path": Path("runtime/other.lock"),
        "orchestration_retention_days": 12,
        "orchestration_compact_history_retention_days": 34,
        "provider_loader": config.provider_loader.model_copy(
            update={"provider_modules": ["custom"]}
        ),
        "addon_loader": config.addon_loader.model_copy(update={"modules": ["custom"]}),
    }.items():
        assert signature(config.model_copy(update={name: value})) != expected
    assert (
        signature(
            config.model_copy(
                update={
                    "provider_loader": config.provider_loader.model_copy(
                        update={"include_builtin": False}
                    )
                }
            )
        )
        != expected
    )


@pytest.mark.parametrize("module", ["evander.voice_sessions", "evander.didww_bridge_protocol"])
@pytest.mark.parametrize(
    "prompt,actionable",
    [
        ("", False),
        ("Ask for approval", False),
        ("Please call the operations contact", False),
        ("Please ASK for the release approval!", True),
        ("Please\nverify  the current release status", True),
        ("Please ask, about the current status", False),
    ],
)
def test_voice_objectives_keep_minimum_words_and_exact_marker_matching(
    module: str, prompt: str, actionable: bool
) -> None:
    assert import_module(module)._voice_task_looks_actionable(prompt) is actionable


@pytest.mark.parametrize(
    "model",
    [AdoBacklogQueryRequest, AdoCurrentIterationBacklogRequest, AdoDailyStatusReportRequest],
)
def test_ado_query_filters_keep_first_spelling_type_aliases_and_validation_order(
    model: type,
) -> None:
    result = model(
        states=[" Active ", "active", "", "DONE"],
        tags_any=[" Team ", "TEAM"],
        types=["pbi", "Product Backlog Item", "story", AdoBacklogItemType.BUG],
    )
    assert result.states == ["Active", "DONE"]
    assert result.tags_any == ["Team"]
    assert result.types == [
        AdoBacklogItemType.PRODUCT_BACKLOG_ITEM,
        AdoBacklogItemType.USER_STORY,
        AdoBacklogItemType.BUG,
    ]
    with pytest.raises(ValidationError) as error:
        model(states="Active", types="Bug", tags_any="Team")
    assert [item["loc"] for item in error.value.errors()] == [
        ("states",),
        ("types",),
        ("tags_any",),
    ]
    assert [item["ctx"]["error"].args[0] for item in error.value.errors()] == [
        "filter must be a list of strings",
        "types must be a list of item types",
        "filter must be a list of strings",
    ]


def test_ado_report_empty_type_lists_keep_distinct_include_and_exclude_defaults() -> None:
    report = AdoDailyStatusReportRequest(types=[], exclude_types=[])
    assert report.types == [AdoBacklogItemType.PRODUCT_BACKLOG_ITEM, AdoBacklogItemType.BUG]
    assert report.exclude_types == []
    assert AdoDailyStatusReportRequest().exclude_types == [AdoBacklogItemType.TASK]
    assert AdoBacklogQueryRequest(types=None).types == []
    assert AdoCurrentIterationBacklogRequest(types=None).types == []
