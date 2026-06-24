from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

from truman_team_addon.templates import TeamTemplate, get_template, template_catalog


def test_template_catalog_exposes_validated_auto_dev_squad() -> None:
    catalog = template_catalog()

    assert [template.id for template in catalog] == [
        "auto-dev-squad",
        "accounting-department",
    ]

    template = get_template("auto-dev-squad")
    assert template is not None
    assert get_template("missing") is None

    payload = template.model_dump(mode="json")
    json.loads(json.dumps(payload))

    assert payload["name"] == "Auto-Dev Squad"
    assert payload["root_role_key"] == "manager"
    assert [role["key"] for role in payload["roles"]] == [
        "manager",
        "project_lead",
        "implementer",
        "reviewer",
        "release_operator",
    ]
    assert {
        role["key"]: role.get("reports_to_role_key") for role in payload["roles"]
    } == {
        "manager": None,
        "project_lead": "manager",
        "implementer": "project_lead",
        "reviewer": "project_lead",
        "release_operator": "project_lead",
    }
    assert {
        role["key"]: role["ephemeral_policy"]["max_concurrent"] for role in payload["roles"]
    } == {
        "manager": 0,
        "project_lead": 1,
        "implementer": 2,
        "reviewer": 1,
        "release_operator": 1,
    }

    required_fields = {field["path"]: field for field in payload["required_fields"]}
    assert required_fields["team.id"]["required"] is True
    assert required_fields["team.operator_communication_target_id"]["required"] is False
    assert required_fields["project.git.host_repo_path"]["required"] is False
    assert required_fields["release_operator.session_id"]["required"] is True

    assert [goal["id"] for goal in payload["project_goals"]] == [
        "auto-dev-reviewed-delivery"
    ]
    assert [task["id"] for task in payload["tasks"]] == [
        "implement-scoped-slice",
        "release-handoff",
    ]
    assert payload["tasks"][1]["dependency_task_ids"] == ["implement-scoped-slice"]
    assert [profile["id"] for profile in payload["review_profiles"]] == ["auto-dev-review"]
    assert [criterion["id"] for criterion in payload["review_profiles"][0]["criteria"]] == [
        "scope",
        "tests",
        "risk",
        "handoff",
    ]


def test_template_catalog_exposes_validated_accounting_department() -> None:
    template = get_template("accounting-department")
    assert template is not None

    payload = template.model_dump(mode="json")
    json.loads(json.dumps(payload))

    assert payload["name"] == "Accounting Department"
    assert payload["root_role_key"] == "manager"
    assert [role["key"] for role in payload["roles"]] == [
        "manager",
        "project_lead",
        "bookkeeping_clerk",
        "invoice_reviewer",
        "payment_preparer",
        "tax_compliance_reviewer",
    ]
    assert {
        role["key"]: role.get("reports_to_role_key") for role in payload["roles"]
    } == {
        "manager": None,
        "project_lead": "manager",
        "bookkeeping_clerk": "project_lead",
        "invoice_reviewer": "project_lead",
        "payment_preparer": "project_lead",
        "tax_compliance_reviewer": "project_lead",
    }
    assert {
        role["key"]: role["permissions"] for role in payload["roles"]
    } == {
        "manager": ["create_root_task"],
        "project_lead": ["create_root_task", "prepare_bookkeeping_action"],
        "bookkeeping_clerk": ["prepare_bookkeeping_action"],
        "invoice_reviewer": ["review_bookkeeping_action"],
        "payment_preparer": [
            "prepare_bookkeeping_action",
            "request_payment_preparation",
        ],
        "tax_compliance_reviewer": ["review_bookkeeping_action"],
    }

    required_fields = {field["path"]: field for field in payload["required_fields"]}
    assert "project.git.host_repo_path" not in required_fields
    assert required_fields["bookkeeping_clerk.session_id"]["required"] is True
    assert required_fields["team.operator_communication_target_id"]["required"] is False

    assert [goal["id"] for goal in payload["project_goals"]] == [
        "bookkeeping-operations"
    ]
    assert [task["id"] for task in payload["tasks"]] == [
        "process-inbox-receipts",
        "reconcile-supplier-invoices",
        "review-prepared-bookkeeping-actions",
        "prepare-bank-payments",
        "review-tax-compliance-items",
    ]
    assert payload["tasks"][2]["dependency_task_ids"] == ["process-inbox-receipts"]
    assert payload["tasks"][3]["dependency_task_ids"] == ["reconcile-supplier-invoices"]
    assert [profile["id"] for profile in payload["review_profiles"]] == [
        "bookkeeping-action-review"
    ]
    assert payload["review_profiles"][0]["applicable_role_keys"] == [
        "invoice_reviewer",
        "tax_compliance_reviewer",
    ]
    assert [criterion["id"] for criterion in payload["review_profiles"][0]["criteria"]] == [
        "supporting_documentation",
        "bokio_mapping",
        "tax_treatment",
        "approval_boundary",
    ]


def test_team_template_rejects_missing_required_builtin_roles() -> None:
    payload = template_catalog()[0].model_dump(mode="json")
    payload["roles"] = [role for role in payload["roles"] if role["key"] != "reviewer"]

    with pytest.raises(ValidationError, match="missing required role keys: reviewer"):
        TeamTemplate.model_validate(payload)


@pytest.mark.parametrize(
    ("field_name", "mutate"),
    [
        ("role keys", lambda payload: payload["roles"].append(copy.deepcopy(payload["roles"][0]))),
        ("task ids", lambda payload: payload["tasks"].append(copy.deepcopy(payload["tasks"][0]))),
        (
            "review profile ids",
            lambda payload: payload["review_profiles"].append(
                copy.deepcopy(payload["review_profiles"][0])
            ),
        ),
    ],
)
def test_team_template_rejects_duplicate_keys(field_name: str, mutate) -> None:
    payload = template_catalog()[0].model_dump(mode="json")
    mutate(payload)

    with pytest.raises(ValidationError, match=f"{field_name} contains duplicate ids"):
        TeamTemplate.model_validate(payload)
