"""Opt-in contracts for host-owned integrations contributed by add-ons.

Registration is descriptive. It never resolves credentials, probes a provider,
starts a worker, or installs a workflow. Existing AddonRegistration fields remain
the extension points for tools, nodes, routes, and connector drivers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import re
from threading import Event
from typing import TYPE_CHECKING, Any, Literal, Protocol

from jsonschema import Draft202012Validator

if TYPE_CHECKING:
    from evander.addons import AddonContext
    from evander.workflow.packs import WorkflowPack

_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
ReadinessStatus = Literal["ready", "blocked", "unknown"]


def _descriptor(value: Any, *callbacks: str) -> None:
    if not isinstance(value.id, str) or not _ID.fullmatch(value.id):
        raise ValueError("integration contribution id must be a lowercase identifier")
    if not isinstance(value.label, str) or not value.label.strip():
        raise ValueError("integration contribution label must be non-empty")
    if hasattr(value, "required") and type(value.required) is not bool:
        raise ValueError("integration contribution required must be a boolean")
    for name in callbacks:
        if not callable(getattr(value, name)):
            raise ValueError(f"integration contribution {value.id!r} requires callable {name}")


def _schema(value: Mapping[str, Any]) -> dict[str, Any]:
    schema = deepcopy(dict(value))
    if schema.get("type") != "object":
        raise ValueError("integration input schema must have type object")

    # Validation must not fetch schemas from an external service or local file.
    def check_refs(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if key in {"$ref", "$dynamicRef"} and (
                    not isinstance(item, str) or not item.startswith("#")
                ):
                    raise ValueError("integration schemas must use local references")
                check_refs(item)
        elif isinstance(node, list):
            for item in node:
                check_refs(item)

    check_refs(schema)
    Draft202012Validator.check_schema(schema)
    return schema


def validate_integration_input(schema: Mapping[str, Any], value: Mapping[str, Any]) -> None:
    """Validate without including submitted values in diagnostics."""
    if not isinstance(value, Mapping):
        raise ValueError("integration input must be an object")
    errors = list(Draft202012Validator(schema).iter_errors(dict(value)))
    if errors:
        rules = sorted({str(error.validator) for error in errors})
        raise ValueError(f"integration input failed schema validation: {', '.join(rules)}")


@dataclass(frozen=True)
class IntegrationConfigSection:
    id: str
    label: str
    schema: Mapping[str, Any]
    description: str = ""

    def __post_init__(self) -> None:
        _descriptor(self)
        object.__setattr__(self, "schema", _schema(self.schema))


@dataclass(frozen=True)
class IntegrationCredentialProvider:
    id: str
    label: str
    is_configured: Callable[[AddonContext], bool] = field(repr=False)
    resolve: Callable[[AddonContext], Mapping[str, str]] = field(repr=False)
    required: bool = True

    def __post_init__(self) -> None:
        _descriptor(self, "is_configured", "resolve")


@dataclass(frozen=True)
class IntegrationCheckResult:
    status: ReadinessStatus
    code: str

    def __post_init__(self) -> None:
        if self.status not in {"ready", "blocked", "unknown"}:
            raise ValueError("invalid integration readiness status")
        if not isinstance(self.code, str) or not _ID.fullmatch(self.code):
            raise ValueError("readiness code must be a lowercase identifier")


@dataclass(frozen=True)
class IntegrationReadinessCheck:
    id: str
    label: str
    check: Callable[[AddonContext], IntegrationCheckResult] = field(repr=False)
    required: bool = True

    def __post_init__(self) -> None:
        _descriptor(self, "check")


class IntegrationWorker(Protocol):
    """A host supervisor owns the stop event and must await worker shutdown.

    Implementations bound their I/O and stop promptly when the event is set.
    Registering or inspecting a worker never runs it.
    """

    def run(self, stop: Event) -> None: ...


@dataclass(frozen=True)
class IntegrationBackgroundWorker:
    id: str
    label: str
    create: Callable[[AddonContext], IntegrationWorker] = field(repr=False)
    description: str = ""

    def __post_init__(self) -> None:
        _descriptor(self, "create")


@dataclass(frozen=True)
class IntegrationStarterVerification:
    """Evidence required after activation; a completion node must follow real work."""

    trigger_node_ids: tuple[str, ...]
    completion_node_id: str
    instructions: str
    manual_trigger_node_id: str | None = None
    required_tool: str | None = None
    required_tool_arguments: Mapping[str, Any] = field(default_factory=dict)
    follow_workflow_completion: bool = False


@dataclass(frozen=True)
class IntegrationWorkflowStarter:
    id: str
    label: str
    input_schema: Mapping[str, Any]
    build: Callable[[AddonContext, Mapping[str, Any]], WorkflowPack | Mapping[str, Any]] = field(
        repr=False
    )
    description: str = ""
    verification: Callable[[AddonContext, Mapping[str, Any]], IntegrationStarterVerification] | None = field(
        default=None, repr=False
    )

    def __post_init__(self) -> None:
        _descriptor(self, "build")
        object.__setattr__(self, "input_schema", _schema(self.input_schema))
        if self.verification is not None and not callable(self.verification):
            raise ValueError("starter verification must be callable")


@dataclass(frozen=True)
class AddonIntegration:
    """Versioned, additive declarations. IDs are local to their owning add-on."""

    contract_version: int = 1
    required_addons: tuple[str, ...] = ()
    settings: tuple[IntegrationConfigSection, ...] = ()
    credentials: tuple[IntegrationCredentialProvider, ...] = ()
    readiness_checks: tuple[IntegrationReadinessCheck, ...] = ()
    workers: tuple[IntegrationBackgroundWorker, ...] = ()
    workflow_starters: tuple[IntegrationWorkflowStarter, ...] = ()

    def __post_init__(self) -> None:
        if type(self.contract_version) is not int or self.contract_version != 1:
            raise ValueError("unsupported integration contract version")
        if isinstance(self.required_addons, str):
            raise ValueError("required_addons must be a sequence of add-on identifiers")
        dependencies = tuple(self.required_addons)
        if any(not isinstance(item, str) or not _ID.fullmatch(item) for item in dependencies):
            raise ValueError("required_addons must contain add-on identifiers")
        if len(set(dependencies)) != len(dependencies):
            raise ValueError("duplicate required add-on")
        object.__setattr__(self, "required_addons", dependencies)
        for name, kind in (
            ("settings", IntegrationConfigSection),
            ("credentials", IntegrationCredentialProvider),
            ("readiness_checks", IntegrationReadinessCheck),
            ("workers", IntegrationBackgroundWorker),
            ("workflow_starters", IntegrationWorkflowStarter),
        ):
            values = tuple(
                item if isinstance(item, kind) else kind(**dict(item))
                for item in getattr(self, name)
            )
            if len({item.id for item in values}) != len(values):
                raise ValueError(f"duplicate integration contribution in {name}")
            object.__setattr__(self, name, values)

    def describe(self) -> dict[str, Any]:
        def row(value: Any) -> dict[str, Any]:
            result = {"id": value.id, "label": value.label}
            for name in ("description", "required", "schema", "input_schema"):
                if hasattr(value, name):
                    result[name] = deepcopy(getattr(value, name))
            return result

        return {
            "contract_version": self.contract_version,
            "required_addons": list(self.required_addons),
            **{
                name: [row(item) for item in getattr(self, name)]
                for name in (
                    "settings",
                    "credentials",
                    "readiness_checks",
                    "workers",
                    "workflow_starters",
                )
            },
        }
