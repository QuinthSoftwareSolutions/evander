from __future__ import annotations

from types import SimpleNamespace

from evander.admin.services import (
    ModelUsageAdminService,
    WorkflowGenerationAdminDependencies,
    WorkflowGenerationAdminService,
)


def test_model_usage_service_validates_operator_steering_messages() -> None:
    assert ModelUsageAdminService._validate_operator_steering_message(" go ") == "go"

    try:
        ModelUsageAdminService._validate_operator_steering_message("   ")
    except ValueError as exc:
        assert "message is required" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected blank operator steering messages to be rejected")


def test_workflow_generation_service_enforces_in_flight_and_cooldown() -> None:
    service = WorkflowGenerationAdminService(
        WorkflowGenerationAdminDependencies(
            config=SimpleNamespace(),
            runtime=SimpleNamespace(),
            workflow_config_admin=SimpleNamespace(),
            normalize_directory_reference=lambda value: value or "root",
            read_assistant_settings=lambda: {},
        )
    )

    service._begin_workflow_generation_request()
    try:
        try:
            service._begin_workflow_generation_request()
        except ValueError as exc:
            assert "already in progress" in str(exc)
        else:  # pragma: no cover - defensive assertion
            raise AssertionError("expected in-flight workflow generation to be rejected")
    finally:
        service._finish_workflow_generation_request()

    try:
        service._begin_workflow_generation_request()
    except ValueError as exc:
        assert "cooling down" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected cooldown workflow generation to be rejected")
