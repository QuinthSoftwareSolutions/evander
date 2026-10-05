"""Directory-owned event contracts. Publication deliberately does not use this validator."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import cached_property
from hashlib import sha256
import json
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, Field, field_validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from evander.events.internal import validate_internal_event_type

if TYPE_CHECKING:
    from evander.config import GatewayConfig
    from evander.directory_config import DirectoryConfigResolver
    from evander.workflow.catalog import WorkflowInternalEventCatalogEntry

DIALECT = "https://json-schema.org/draft/2020-12/schema"


def revision(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def check_payload_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if schema.get("$schema", DIALECT) != DIALECT:
        raise ValueError("Event schemas must use JSON Schema Draft 2020-12")
    if schema.get("type") != "object":
        raise ValueError("Event payload schemas must declare type: object")
    try:
        Draft202012Validator.check_schema(schema)
        resolver = (
            Registry()
            .with_resource(
                "urn:evander:event-schema",
                Resource.from_contents(schema, default_specification=DRAFT202012),
            )
            .resolver("urn:evander:event-schema")
        )

        visited: set[int] = set()

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                if id(value) in visited:
                    return
                visited.add(id(value))
                for key, child in value.items():
                    if key in {"$ref", "$dynamicRef"}:
                        if not isinstance(child, str) or not child.startswith("#"):
                            raise ValueError(
                                "Event schema references must stay within the document"
                            )
                        target = resolver.lookup(child).contents
                        Draft202012Validator.check_schema(target)
                        visit(target)
                    # Only schema-bearing keywords are visited: examples/defaults are data.
                    elif key in {
                        "properties",
                        "$defs",
                        "definitions",
                        "patternProperties",
                        "dependentSchemas",
                    }:
                        for subschema in child.values():
                            visit(subschema)
                    elif key in {
                        "items",
                        "additionalProperties",
                        "unevaluatedProperties",
                        "unevaluatedItems",
                        "contains",
                        "not",
                        "if",
                        "then",
                        "else",
                        "propertyNames",
                    }:
                        visit(child)
                    elif key in {"allOf", "anyOf", "oneOf", "prefixItems"}:
                        for subschema in child:
                            visit(subschema)

        visit(schema)
        revision(schema)  # Require JSON values, including finite numbers.
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Invalid event payload schema: {exc}") from exc
    return schema


class EventDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1)
    description: str | None = None
    payload_schema: dict[str, Any]

    _schema = field_validator("payload_schema")(check_payload_schema)


class EventDirectoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: dict[str, EventDefinition] = Field(default_factory=dict)

    @field_validator("events")
    @classmethod
    def validate_names(cls, value: dict[str, EventDefinition]) -> dict[str, EventDefinition]:
        for name in value:
            if validate_internal_event_type(name) != name:
                raise ValueError("Event types must not have surrounding whitespace")
        return value


class EventPayloadInvalid(ValueError):
    def __init__(self, failures: list[dict[str, Any]]) -> None:
        super().__init__("Event payload does not match its registered schema")
        self.failures = failures


def validate_payload(schema: dict[str, Any], payload: Any) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    validator = Draft202012Validator(schema, registry=Registry(), format_checker=FormatChecker())
    try:
        errors = list(validator.iter_errors(payload))
    except Exception as exc:
        raise ValueError("The registered event schema could not be evaluated") from exc
    for error in errors:
        paths = [list(error.absolute_path)]
        if error.validator == "required" and isinstance(error.instance, dict):
            paths = [
                list(error.absolute_path) + [name]
                for name in error.validator_value
                if name not in error.instance
            ]
        for path in paths:
            failures.append(
                {
                    "code": f"schema.{error.validator}",
                    "path": "".join(
                        "/" + str(part).replace("~", "~0").replace("/", "~1") for part in path
                    ),
                    "message": f"{path[-1]} is required."
                    if error.validator == "required"
                    else error.message,
                }
            )
    return sorted(
        {
            (failure["path"], failure["code"], failure["message"]): failure for failure in failures
        }.values(),
        key=lambda failure: (failure["path"], failure["code"], failure["message"]),
    )


class EventSchemaService:
    def __init__(
        self,
        config: GatewayConfig,
        defaults: Callable[[], Iterable[WorkflowInternalEventCatalogEntry]],
    ) -> None:
        self.config = config
        self.defaults = defaults

    @cached_property
    def resolver(self) -> DirectoryConfigResolver:
        from evander.provider_config import ProviderConfigResolver

        return ProviderConfigResolver(self.config).resolver

    def catalog(self, directory_path: str) -> list[dict[str, Any]]:
        entries = {entry.event_type: entry.model_dump(mode="json") for entry in self.defaults()}
        resolution = self.resolver.resolve_domain(
            directory_path, domain="events", base_config={"events": {}}
        )
        for layer in resolution.applied_layers:
            definitions = EventDirectoryConfig.model_validate(layer.overlay)
            for name, definition in definitions.events.items():
                entries[name] = {
                    "event_type": name,
                    **definition.model_dump(mode="json"),
                    "source": layer.directory_path,
                }
        for entry in entries.values():
            entry["schema_revision"] = revision([entry["event_type"], entry.get("payload_schema")])
        return sorted(entries.values(), key=lambda entry: entry["event_type"])

    def definition(self, directory_path: str, event_type: str) -> dict[str, Any]:
        return next(
            (entry for entry in self.catalog(directory_path) if entry["event_type"] == event_type),
            {
                "event_type": event_type,
                "label": event_type,
                "payload_schema": None,
                "schema_revision": revision([event_type, None]),
                "source": None,
            },
        )

    def validate(self, directory_path: str, event_type: str, payload: Any) -> dict[str, Any]:
        definition = self.definition(directory_path, event_type)
        schema = definition.get("payload_schema")
        failures = (
            validate_payload(schema, payload)
            if schema is not None
            else [
                {
                    "code": "schema.not_registered",
                    "path": "",
                    "message": f"No schema registered for {event_type}",
                }
            ]
        )
        return {
            "valid": not failures,
            "event_type": event_type,
            "schema_revision": definition["schema_revision"],
            "schema_source": definition.get("source"),
            "failures": failures,
        }
