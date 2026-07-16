from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from evander_team_addon.models import (
    EphemeralAgentPolicy,
    ProjectGoal,
    ReviewProfileCriterion,
    RolePermission,
    TeamModel,
    TeamTaskState,
    _ensure_unique,
)


class TemplateModel(TeamModel):
    pass


class TemplateRequiredField(TemplateModel):
    path: str
    label: str
    description: str = ""
    required: bool = True
    example: Any | None = None

    @field_validator("path", "label", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("description", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str:
        return _optional_text(value) or ""


class TemplateRole(TemplateModel):
    key: str
    label: str
    description: str = ""
    reports_to_role_key: str | None = None
    permissions: list[RolePermission] = Field(default_factory=list)
    ephemeral_policy: EphemeralAgentPolicy = Field(default_factory=EphemeralAgentPolicy)

    @field_validator("key", "label", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("description", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str:
        return _optional_text(value) or ""

    @field_validator("reports_to_role_key", mode="before")
    @classmethod
    def _optional_role_key(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("permissions", mode="before")
    @classmethod
    def _permissions(cls, value: object) -> list[RolePermission]:
        if isinstance(value, (list, tuple)):
            return [
                item if isinstance(item, RolePermission) else RolePermission(str(item))
                for item in value
            ]
        items = _unique_text_list(value, "permissions")
        return [RolePermission(item) for item in items]


class TemplateTask(TemplateModel):
    id: str
    title: str
    description: str
    owner_role_key: str
    created_by_role_key: str
    priority: int = Field(default=10, ge=0)
    state: TeamTaskState = TeamTaskState.PENDING
    parent_task_id: str | None = None
    dependency_task_ids: list[str] = Field(default_factory=list)

    @field_validator("id", "title", "description", "owner_role_key", "created_by_role_key", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("parent_task_id", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("dependency_task_ids", mode="before")
    @classmethod
    def _dependency_task_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value, "dependency_task_ids")


class TemplateReviewProfile(TemplateModel):
    id: str
    name: str
    description: str = ""
    project_scoped: bool = True
    applicable_role_keys: list[str] = Field(default_factory=list)
    criteria: list[ReviewProfileCriterion] = Field(default_factory=list)

    @field_validator("id", "name", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("description", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str:
        return _optional_text(value) or ""

    @field_validator("applicable_role_keys", mode="before")
    @classmethod
    def _applicable_role_keys(cls, value: object) -> list[str]:
        return _unique_text_list(value, "applicable_role_keys")

    @model_validator(mode="after")
    def _validate_criteria(self) -> "TemplateReviewProfile":
        _ensure_unique([criterion.id for criterion in self.criteria], "review profile criteria")
        return self


class TeamTemplate(TemplateModel):
    id: str
    name: str
    description: str = ""
    root_role_key: str
    required_role_keys: list[str] = Field(default_factory=list)
    roles: list[TemplateRole] = Field(default_factory=list)
    required_fields: list[TemplateRequiredField] = Field(default_factory=list)
    project_goals: list[ProjectGoal] = Field(default_factory=list)
    tasks: list[TemplateTask] = Field(default_factory=list)
    review_profiles: list[TemplateReviewProfile] = Field(default_factory=list)

    @field_validator("id", "name", "root_role_key", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("description", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str:
        return _optional_text(value) or ""

    @field_validator("required_role_keys", mode="before")
    @classmethod
    def _required_role_keys(cls, value: object) -> list[str]:
        return _unique_text_list(value, "required_role_keys")

    @model_validator(mode="after")
    def _validate_catalog_shape(self) -> "TeamTemplate":
        _ensure_unique([role.key for role in self.roles], "role keys")
        _ensure_unique([field.path for field in self.required_fields], "required field paths")
        _ensure_unique([goal.id for goal in self.project_goals], "project goal ids")
        _ensure_unique([task.id for task in self.tasks], "task ids")
        _ensure_unique([profile.id for profile in self.review_profiles], "review profile ids")

        role_map = {role.key: role for role in self.roles}
        if self.root_role_key not in role_map:
            raise ValueError(f"root role {self.root_role_key} is missing from the template")

        root_keys = [role.key for role in self.roles if role.reports_to_role_key is None]
        if root_keys != [self.root_role_key]:
            raise ValueError(
                f"template must define only root role {self.root_role_key}; "
                f"found roots: {', '.join(sorted(root_keys)) or 'none'}"
            )

        missing_required = [key for key in self.required_role_keys if key not in role_map]
        extra_roles = [key for key in role_map if key not in self.required_role_keys]
        if missing_required or extra_roles:
            details: list[str] = []
            if missing_required:
                details.append(f"missing required role keys: {', '.join(sorted(missing_required))}")
            if extra_roles:
                details.append(f"unexpected role keys: {', '.join(sorted(extra_roles))}")
            raise ValueError("; ".join(details))

        for role in self.roles:
            if role.reports_to_role_key is not None and role.reports_to_role_key not in role_map:
                raise ValueError(
                    f"role {role.key} reports to missing role {role.reports_to_role_key}"
                )

        for role_key in role_map:
            seen: set[str] = set()
            current_key: str | None = role_key
            while current_key is not None:
                if current_key in seen:
                    raise ValueError(f"role hierarchy cycle includes {current_key}")
                seen.add(current_key)
                current_role = role_map[current_key]
                current_key = current_role.reports_to_role_key

        task_ids = {task.id for task in self.tasks}
        for task in self.tasks:
            if task.owner_role_key not in role_map:
                raise ValueError(f"task {task.id} references missing owner role {task.owner_role_key}")
            if task.created_by_role_key not in role_map:
                raise ValueError(
                    f"task {task.id} references missing creator role {task.created_by_role_key}"
                )
            if task.parent_task_id is not None and task.parent_task_id not in task_ids:
                raise ValueError(f"task {task.id} references missing parent task {task.parent_task_id}")
            for dependency_id in task.dependency_task_ids:
                if dependency_id not in task_ids:
                    raise ValueError(
                        f"task {task.id} references missing dependency task {dependency_id}"
                    )

        for profile in self.review_profiles:
            for role_key in profile.applicable_role_keys:
                if role_key not in role_map:
                    raise ValueError(
                        f"review profile {profile.id} references missing role {role_key}"
                    )

        return self


_AUTO_DEV_SQUAD_TEMPLATE = {
    "id": "auto-dev-squad",
    "name": "Auto-Dev Squad",
    "description": (
        "Opinionated manager/lead/implementer/reviewer/release-operator squad for "
        "reviewed auto-dev delivery."
    ),
    "root_role_key": "manager",
    "required_role_keys": [
        "manager",
        "project_lead",
        "implementer",
        "reviewer",
        "release_operator",
    ],
    "roles": [
        {
            "key": "manager",
            "label": "Manager",
            "description": "Owns operator communication and overall coordination.",
            "permissions": ["create_root_task"],
            "ephemeral_policy": {"max_concurrent": 0},
        },
        {
            "key": "project_lead",
            "label": "Project Lead",
            "description": "Owns the project plan and task routing.",
            "reports_to_role_key": "manager",
            "permissions": ["create_root_task"],
            "ephemeral_policy": {"max_concurrent": 1},
        },
        {
            "key": "implementer",
            "label": "Implementer",
            "description": "Builds the scoped change and keeps the implementation tight.",
            "reports_to_role_key": "project_lead",
            "ephemeral_policy": {"max_concurrent": 2},
        },
        {
            "key": "reviewer",
            "label": "Reviewer",
            "description": "Checks scope, tests, and risk before delivery.",
            "reports_to_role_key": "project_lead",
            "ephemeral_policy": {"max_concurrent": 1},
        },
        {
            "key": "release_operator",
            "label": "Release Operator",
            "description": "Handles release handoff and publication.",
            "reports_to_role_key": "project_lead",
            "permissions": ["request_release"],
            "ephemeral_policy": {"max_concurrent": 1},
        },
    ],
    "required_fields": [
        {
            "path": "team.id",
            "label": "Team ID",
            "description": "Stable identifier for the new team.",
            "example": "team-auto-dev-squad",
        },
        {
            "path": "team.name",
            "label": "Team Name",
            "description": "Human-readable team name.",
            "example": "Auto-Dev Squad",
        },
        {
            "path": "team.operator_communication_target_id",
            "label": "Operator Communication Target",
            "description": "Optional target for manager escalations.",
            "required": False,
            "example": "slack://ops-channel",
        },
        {
            "path": "team.escalate_to_governance",
            "label": "Escalate to Governance",
            "description": "Route manager escalations to governance group agenda instead of operator escalation.",
            "required": False,
            "example": "false",
        },
        {
            "path": "project.id",
            "label": "Project ID",
            "description": "Stable project identifier.",
            "example": "project-auto-dev-squad",
        },
        {
            "path": "project.name",
            "label": "Project Name",
            "description": "Human-readable project name.",
            "example": "Reviewed Auto-Dev Delivery",
        },
        {
            "path": "project.git.managed_repo_id",
            "label": "Managed Git Repository ID",
            "description": "Registered Evander managed repository.",
            "required": False,
            "example": "mr_0123456789abcdef0123",
        },
        {
            "path": "project.git.base_branch",
            "label": "Git Base Branch",
            "description": "Optional upstream branch to mirror.",
            "required": False,
            "example": "main",
        },
        {
            "path": "manager.agent_id",
            "label": "Manager Agent ID",
            "description": "Persistent manager agent identifier.",
            "example": "manager-auto-dev-squad",
        },
        {
            "path": "manager.session_id",
            "label": "Manager Session ID",
            "description": "Persistent manager session identifier.",
            "example": "session-manager-auto-dev-squad",
        },
        {
            "path": "project_lead.agent_id",
            "label": "Project Lead Agent ID",
            "description": "Persistent project lead agent identifier.",
            "example": "project-lead-auto-dev-squad",
        },
        {
            "path": "project_lead.session_id",
            "label": "Project Lead Session ID",
            "description": "Persistent project lead session identifier.",
            "example": "session-project-lead-auto-dev-squad",
        },
        {
            "path": "implementer.agent_id",
            "label": "Implementer Agent ID",
            "description": "Persistent implementer agent identifier.",
            "example": "implementer-auto-dev-squad",
        },
        {
            "path": "implementer.session_id",
            "label": "Implementer Session ID",
            "description": "Persistent implementer session identifier.",
            "example": "session-implementer-auto-dev-squad",
        },
        {
            "path": "reviewer.agent_id",
            "label": "Reviewer Agent ID",
            "description": "Persistent reviewer agent identifier.",
            "example": "reviewer-auto-dev-squad",
        },
        {
            "path": "reviewer.session_id",
            "label": "Reviewer Session ID",
            "description": "Persistent reviewer session identifier.",
            "example": "session-reviewer-auto-dev-squad",
        },
        {
            "path": "release_operator.agent_id",
            "label": "Release Operator Agent ID",
            "description": "Persistent release operator agent identifier.",
            "example": "release-operator-auto-dev-squad",
        },
        {
            "path": "release_operator.session_id",
            "label": "Release Operator Session ID",
            "description": "Persistent release operator session identifier.",
            "example": "session-release-operator-auto-dev-squad",
        },
    ],
    "project_goals": [
        {
            "id": "auto-dev-reviewed-delivery",
            "description": "Ship a reviewed auto-dev increment through implementation, review, and handoff.",
            "state": "pending",
        }
    ],
    "tasks": [
        {
            "id": "implement-scoped-slice",
            "title": "Implement the scoped slice",
            "description": (
                "Build the bounded change, add tests, and keep the implementation narrow "
                "enough for review."
            ),
            "owner_role_key": "implementer",
            "created_by_role_key": "project_lead",
            "priority": 10,
        },
        {
            "id": "release-handoff",
            "title": "Prepare release handoff",
            "description": (
                "Package release notes, verify artifacts, and hand off the reviewed result."
            ),
            "owner_role_key": "release_operator",
            "created_by_role_key": "project_lead",
            "priority": 20,
            "dependency_task_ids": ["implement-scoped-slice"],
        },
    ],
    "review_profiles": [
        {
            "id": "auto-dev-review",
            "name": "Auto-Dev Delivery Review",
            "description": (
                "Checks that the slice stays in scope, automated tests exist, risk is "
                "understood, and the release handoff is ready."
            ),
            "project_scoped": True,
            "applicable_role_keys": ["reviewer"],
            "criteria": [
                {
                    "id": "scope",
                    "label": "Scope",
                    "description": "The deliverable stays inside the requested slice.",
                    "required": True,
                },
                {
                    "id": "tests",
                    "label": "Tests",
                    "description": "Automated checks were added or updated and pass.",
                    "required": True,
                },
                {
                    "id": "risk",
                    "label": "Risk",
                    "description": "Edge cases and rollback risk are called out.",
                    "required": True,
                },
                {
                    "id": "handoff",
                    "label": "Release Handoff",
                    "description": "Release notes and artifacts are ready to publish.",
                    "required": True,
                },
            ],
        }
    ],
}


_ACCOUNTING_DEPARTMENT_TEMPLATE = {
    "id": "accounting-department",
    "name": "Accounting Department",
    "description": (
        "Department template for bookkeeping operations that coordinates Bokio and "
        "Skatteverket-assisted accounting work without storing ledger state in Team."
    ),
    "root_role_key": "manager",
    "required_role_keys": [
        "manager",
        "project_lead",
        "bookkeeping_clerk",
        "invoice_reviewer",
        "payment_preparer",
        "tax_compliance_reviewer",
    ],
    "roles": [
        {
            "key": "manager",
            "label": "Accounting Manager",
            "description": "Owns accounting priorities, operator communication, and policy escalation.",
            "permissions": ["create_root_task"],
            "ephemeral_policy": {"max_concurrent": 0},
        },
        {
            "key": "project_lead",
            "label": "Bookkeeping Lead",
            "description": "Plans bookkeeping operations and routes work across accounting specialists.",
            "reports_to_role_key": "manager",
            "permissions": ["create_root_task", "prepare_bookkeeping_action"],
            "ephemeral_policy": {"max_concurrent": 1},
        },
        {
            "key": "bookkeeping_clerk",
            "label": "Bookkeeping Clerk",
            "description": "Inspects receipts, Bokio records, invoices, and prepares draft bookkeeping actions.",
            "reports_to_role_key": "project_lead",
            "permissions": ["prepare_bookkeeping_action"],
            "ephemeral_policy": {"max_concurrent": 1},
        },
        {
            "key": "invoice_reviewer",
            "label": "Invoice Reviewer",
            "description": "Reviews supplier invoices, invoice categorization, and prepared journal entries.",
            "reports_to_role_key": "project_lead",
            "permissions": ["review_bookkeeping_action"],
            "ephemeral_policy": {"max_concurrent": 1},
        },
        {
            "key": "payment_preparer",
            "label": "Payment Preparer",
            "description": "Prepares Bokio bank-payment drafts for human approval and BankID signing.",
            "reports_to_role_key": "project_lead",
            "permissions": ["prepare_bookkeeping_action", "request_payment_preparation"],
            "ephemeral_policy": {"max_concurrent": 1},
        },
        {
            "key": "tax_compliance_reviewer",
            "label": "Tax Compliance Reviewer",
            "description": "Reviews VAT, tax-account, and filing-sensitive accounting questions.",
            "reports_to_role_key": "project_lead",
            "permissions": ["review_bookkeeping_action"],
            "ephemeral_policy": {"max_concurrent": 1},
        },
    ],
    "required_fields": [
        {
            "path": "team.id",
            "label": "Team ID",
            "description": "Stable identifier for the accounting team.",
            "example": "team-accounting",
        },
        {
            "path": "team.name",
            "label": "Team Name",
            "description": "Human-readable accounting department name.",
            "example": "Accounting Department",
        },
        {
            "path": "team.operator_communication_target_id",
            "label": "Operator Communication Target",
            "description": "Optional target for manager escalations about missing approvals or policy questions.",
            "required": False,
            "example": "teams://accounting-ops",
        },
        {
            "path": "team.escalate_to_governance",
            "label": "Escalate to Governance",
            "description": "Route manager escalations to governance agenda instead of operator escalation.",
            "required": False,
            "example": "false",
        },
        {
            "path": "project.id",
            "label": "Project ID",
            "description": "Stable workstream identifier.",
            "example": "project-bookkeeping-operations",
        },
        {
            "path": "project.name",
            "label": "Project Name",
            "description": "Human-readable accounting workstream name.",
            "example": "Bookkeeping Operations",
        },
        {
            "path": "manager.agent_id",
            "label": "Accounting Manager Agent ID",
            "description": "Persistent accounting manager agent identifier.",
            "example": "manager-accounting",
        },
        {
            "path": "manager.session_id",
            "label": "Accounting Manager Session ID",
            "description": "Persistent accounting manager session identifier.",
            "example": "session-manager-accounting",
        },
        {
            "path": "project_lead.agent_id",
            "label": "Bookkeeping Lead Agent ID",
            "description": "Persistent bookkeeping lead agent identifier.",
            "example": "bookkeeping-lead-accounting",
        },
        {
            "path": "project_lead.session_id",
            "label": "Bookkeeping Lead Session ID",
            "description": "Persistent bookkeeping lead session identifier.",
            "example": "session-bookkeeping-lead-accounting",
        },
        {
            "path": "bookkeeping_clerk.agent_id",
            "label": "Bookkeeping Clerk Agent ID",
            "description": "Persistent bookkeeping clerk agent identifier.",
            "example": "bookkeeping-clerk-accounting",
        },
        {
            "path": "bookkeeping_clerk.session_id",
            "label": "Bookkeeping Clerk Session ID",
            "description": "Persistent bookkeeping clerk session identifier.",
            "example": "session-bookkeeping-clerk-accounting",
        },
        {
            "path": "invoice_reviewer.agent_id",
            "label": "Invoice Reviewer Agent ID",
            "description": "Persistent invoice reviewer agent identifier.",
            "example": "invoice-reviewer-accounting",
        },
        {
            "path": "invoice_reviewer.session_id",
            "label": "Invoice Reviewer Session ID",
            "description": "Persistent invoice reviewer session identifier.",
            "example": "session-invoice-reviewer-accounting",
        },
        {
            "path": "payment_preparer.agent_id",
            "label": "Payment Preparer Agent ID",
            "description": "Persistent payment preparer agent identifier.",
            "example": "payment-preparer-accounting",
        },
        {
            "path": "payment_preparer.session_id",
            "label": "Payment Preparer Session ID",
            "description": "Persistent payment preparer session identifier.",
            "example": "session-payment-preparer-accounting",
        },
        {
            "path": "tax_compliance_reviewer.agent_id",
            "label": "Tax Compliance Reviewer Agent ID",
            "description": "Persistent tax compliance reviewer agent identifier.",
            "example": "tax-compliance-reviewer-accounting",
        },
        {
            "path": "tax_compliance_reviewer.session_id",
            "label": "Tax Compliance Reviewer Session ID",
            "description": "Persistent tax compliance reviewer session identifier.",
            "example": "session-tax-compliance-reviewer-accounting",
        },
    ],
    "project_goals": [
        {
            "id": "bookkeeping-operations",
            "description": (
                "Process receipts, supplier invoices, and bookkeeping actions through "
                "review and human approval."
            ),
            "state": "pending",
        }
    ],
    "tasks": [
        {
            "id": "process-inbox-receipts",
            "title": "Process inbox receipts",
            "description": (
                "Review newly received documents, match them to Bokio records where possible, "
                "and prepare draft bookkeeping actions for review."
            ),
            "owner_role_key": "bookkeeping_clerk",
            "created_by_role_key": "project_lead",
            "priority": 10,
        },
        {
            "id": "reconcile-supplier-invoices",
            "title": "Reconcile supplier invoices",
            "description": (
                "Check supplier invoices against Bokio data, flag missing information, "
                "and prepare supplier-invoice drafts when safe."
            ),
            "owner_role_key": "bookkeeping_clerk",
            "created_by_role_key": "project_lead",
            "priority": 20,
        },
        {
            "id": "review-prepared-bookkeeping-actions",
            "title": "Review prepared bookkeeping actions",
            "description": (
                "Review prepared journal entries, supplier invoices, and supporting artifacts "
                "before human approval executes them in Bokio."
            ),
            "owner_role_key": "invoice_reviewer",
            "created_by_role_key": "project_lead",
            "priority": 30,
            "dependency_task_ids": ["process-inbox-receipts"],
        },
        {
            "id": "prepare-bank-payments",
            "title": "Prepare bank payments",
            "description": (
                "Prepare Bokio bank-payment actions for approved payable items; final signing "
                "remains a human BankID step in Bokio."
            ),
            "owner_role_key": "payment_preparer",
            "created_by_role_key": "project_lead",
            "priority": 40,
            "dependency_task_ids": ["reconcile-supplier-invoices"],
        },
        {
            "id": "review-tax-compliance-items",
            "title": "Review tax and compliance items",
            "description": (
                "Review VAT, tax-account, and filing-sensitive questions and escalate any "
                "uncertain accounting policy decisions."
            ),
            "owner_role_key": "tax_compliance_reviewer",
            "created_by_role_key": "project_lead",
            "priority": 50,
        },
    ],
    "review_profiles": [
        {
            "id": "bookkeeping-action-review",
            "name": "Bookkeeping Action Review",
            "description": (
                "Checks Bokio-ready prepared actions for support, account mapping, tax treatment, "
                "and explicit human approval requirements."
            ),
            "project_scoped": True,
            "applicable_role_keys": ["invoice_reviewer", "tax_compliance_reviewer"],
            "criteria": [
                {
                    "id": "supporting_documentation",
                    "label": "Supporting Documentation",
                    "description": "Receipts, invoices, or other source records are linked or called out.",
                    "required": True,
                },
                {
                    "id": "bokio_mapping",
                    "label": "Bokio Mapping",
                    "description": "Accounts, counterparties, amounts, and dates are consistent with Bokio data.",
                    "required": True,
                },
                {
                    "id": "tax_treatment",
                    "label": "Tax Treatment",
                    "description": "VAT and filing-sensitive treatment is checked or explicitly escalated.",
                    "required": True,
                },
                {
                    "id": "approval_boundary",
                    "label": "Approval Boundary",
                    "description": "The action stays prepared-only until a human approval or BankID signing step.",
                    "required": True,
                },
            ],
        }
    ],
}


def template_catalog() -> tuple[TeamTemplate, ...]:
    return tuple(template.model_copy(deep=True) for template in _TEMPLATE_CATALOG)


def get_template(template_id: str) -> TeamTemplate | None:
    normalized_template_id = _required_text(template_id, "template_id")
    for template in _TEMPLATE_CATALOG:
        if template.id == normalized_template_id:
            return template.model_copy(deep=True)
    return None


def _build_template_catalog() -> tuple[TeamTemplate, ...]:
    templates = tuple(TeamTemplate.model_validate(template) for template in _TEMPLATE_DEFINITIONS)
    _ensure_unique([template.id for template in templates], "template ids")
    return templates


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _unique_text_list(value: object, field_name: str) -> list[str]:
    if value is None:
        items: list[str] = []
    elif isinstance(value, str):
        items = [_required_text(value, field_name)]
    elif isinstance(value, (list, tuple)):
        items = [_required_text(item, field_name) for item in value]
    else:
        raise ValueError(f"{field_name} must be a list of strings")
    _ensure_unique(items, field_name)
    return items


_TEMPLATE_DEFINITIONS = (
    _AUTO_DEV_SQUAD_TEMPLATE,
    _ACCOUNTING_DEPARTMENT_TEMPLATE,
)
_TEMPLATE_CATALOG = _build_template_catalog()


__all__ = [
    "TeamTemplate",
    "TemplateRequiredField",
    "TemplateReviewProfile",
    "TemplateRole",
    "TemplateTask",
    "get_template",
    "template_catalog",
]
