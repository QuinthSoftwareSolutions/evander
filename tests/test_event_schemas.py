from copy import deepcopy

import pytest

from evander.config import GatewayConfig
from evander.directories import DirectoryService, DirectoryStore
from evander.directory_config import DirectoryConfigResolver, DirectoryConfigStore
from evander.events.internal import InternalEventBus
from evander.events.schema import EventDefinition, EventSchemaService, validate_payload
from evander.workflow.catalog import WorkflowInternalEventCatalogEntry


@pytest.fixture
def catalog(tmp_path):
    config = GatewayConfig(runtime_dir=tmp_path)
    directories = DirectoryStore(config.directories_store_path)
    service = DirectoryService(directories)
    service.create(parent="root", name="first")
    service.create(parent="root", name="second")
    service.create(parent="root/first", name="child")
    store = DirectoryConfigStore(directories, runtime_dir=tmp_path)
    defaults = [
        WorkflowInternalEventCatalogEntry(
            event_type="custom.answer",
            label="Answer",
            source="Synthetic adapter",
            payload_schema={
                "type": "object",
                "properties": {"accepted": {"type": "boolean"}},
                "required": ["accepted"],
            },
        )
    ]
    return (
        EventSchemaService(config, lambda: defaults),
        store,
        DirectoryConfigResolver(service, config_store=store),
    )


def test_event_definition_replacement_inheritance_and_directory_isolation(catalog):
    schemas, store, resolver = catalog
    store.write(
        "root",
        domain="events",
        payload={
            "events": {
                "custom.answer": {
                    "label": "Root",
                    "payload_schema": {
                        "type": "object",
                        "properties": {"old": {"type": "integer"}},
                        "required": ["old"],
                    },
                }
            }
        },
    )
    child = {
        "events": {
            "custom.answer": {
                "label": "Local",
                "payload_schema": {
                    "type": "object",
                    "properties": {"new": {"type": "string"}},
                    "required": ["new"],
                    "additionalProperties": False,
                },
            }
        }
    }
    store.write("root/first", domain="events", payload=child)
    assert schemas.validate("root/first/child", "custom.answer", {"new": "yes"})["valid"]
    assert not schemas.validate("root/second", "custom.answer", {"new": "yes"})["valid"]
    resolved = resolver.resolve_domain("root/first/child", domain="events").effective_config
    assert (
        resolved["events"]["custom.answer"]["payload_schema"]
        == child["events"]["custom.answer"]["payload_schema"]
    )
    assert schemas.definition("root/first/child", "custom.answer")["source"] == "root/first"
    store.write("root/first", domain="events", payload={})
    assert schemas.validate("root/first/child", "custom.answer", {"old": 2})["valid"]
    store.write("root", domain="events", payload={})
    assert schemas.validate("root/first/child", "custom.answer", {"accepted": False})["valid"]


def test_schema_validation_has_pointer_errors_without_coercion_or_defaults():
    schema = {
        "type": "object",
        "properties": {
            "a/b": {"type": "array", "items": {"type": "integer"}},
            "defaulted": {"type": "boolean", "default": True},
        },
        "required": ["missing"],
    }
    payload = {"a/b": [False, "3"]}
    original = deepcopy(payload)
    failures = validate_payload(schema, payload)
    assert {(entry["path"], entry["code"]) for entry in failures} == {
        ("/a~1b/0", "schema.type"),
        ("/a~1b/1", "schema.type"),
        ("/missing", "schema.required"),
    }
    assert payload == original


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array"},
        {"type": "object", "properties": {"x": {"type": "unknown"}}},
        {"type": "object", "$ref": "https://example.test/schema"},
        {"type": "object", "properties": {"x": {"$ref": "#/missing"}}},
        {
            "type": "object",
            "$ref": "#/definitions/value",
            "definitions": {"value": {"$ref": "https://example.test/schema"}},
        },
        {"type": "object", "$ref": "#/metadata", "metadata": "not a schema"},
        {"type": "object", "$schema": "http://json-schema.org/draft-07/schema#"},
    ],
)
def test_invalid_schema_and_external_references_are_rejected(schema):
    with pytest.raises(ValueError):
        EventDefinition(label="Example", payload_schema=schema)


def test_document_local_refs_formats_and_unregistered_events(catalog):
    schema = {
        "type": "object",
        "$defs": {"email": {"type": "string", "format": "email"}},
        "properties": {"email": {"$ref": "#/$defs/email"}},
        "required": ["email"],
    }
    EventDefinition(label="Example", payload_schema=schema)
    assert not validate_payload(schema, {"email": "operator@example.test"})
    assert validate_payload(schema, {"email": "invalid"})[0]["code"] == "schema.format"
    schemas, _store, _resolver = catalog
    assert (
        schemas.validate("root", "custom.unknown", {})["failures"][0]["code"]
        == "schema.not_registered"
    )


def test_defaults_are_event_data_and_recursive_document_references_are_supported():
    schema = {
        "type": "object",
        "default": {"$ref": "https://example.test/data"},
        "properties": {"child": {"$ref": "#"}},
    }
    EventDefinition(label="Recursive", payload_schema=schema)
    assert not validate_payload(schema, {"child": {"child": {}}})


def test_publication_remains_independent_of_registered_schemas(catalog, tmp_path):
    schemas, _store, _resolver = catalog
    assert not schemas.validate("root", "custom.answer", {"accepted": "yes"})["valid"]
    event = InternalEventBus(tmp_path / "orchestration.db").publish(
        event_type="custom.answer", payload={"accepted": "yes"}
    )
    assert event.payload == {"accepted": "yes"}
