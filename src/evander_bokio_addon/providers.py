from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_sandbox.effects.providers.base import EffectProviderBase
from agent_sandbox.queries.providers.base import QueryProviderBase
from agent_sandbox.workflow.catalog import (
    WorkflowEditorFieldDefinition,
    WorkflowEditorNodeCatalogEntry,
)
from agent_sandbox.workflow.plugins import ProviderPluginRegistration

from .api import _execute_prepared_action
from .client import BokioClient
from .state import PreparedActionStore


def register_providers() -> ProviderPluginRegistration:
    return ProviderPluginRegistration(
        effect_providers={
            "effect:bokio:upload-document": BokioUploadDocumentEffectProvider,
            "effect:bokio:prepare-journal-entry": BokioPrepareJournalEntryEffectProvider,
            "effect:bokio:create-journal-entry": BokioCreateJournalEntryEffectProvider,
            "effect:bokio:prepare-supplier-invoice": BokioPrepareSupplierInvoiceEffectProvider,
            "effect:bokio:create-supplier-invoice": BokioCreateSupplierInvoiceEffectProvider,
            "effect:bokio:prepare-bank-payment": BokioPrepareBankPaymentEffectProvider,
            "effect:bokio:create-bank-payment": BokioCreateBankPaymentEffectProvider,
        },
        query_providers={
            "query:bokio:company": BokioCompanyQueryProvider,
            "query:bokio:chart-of-accounts": BokioChartOfAccountsQueryProvider,
            "query:bokio:journal-entries": BokioJournalEntriesQueryProvider,
            "query:bokio:invoices": BokioInvoicesQueryProvider,
            "query:bokio:supplier-invoices": BokioSupplierInvoicesQueryProvider,
        },
        workflow_editor_catalog_entries=_CATALOG,
    )


class _BokioProviderMixin:
    def _context(self) -> Any:
        runtime_context = getattr(self, "runtime_context", None)
        if runtime_context is None:
            raise RuntimeError("Bokio provider requires runtime context")
        config = getattr(runtime_context, "resolved_config", None)
        if config is None:
            provider_config = getattr(runtime_context, "provider_config_resolver", None)
            config = getattr(provider_config, "config", None)
        return type(
            "BokioProviderContext",
            (),
            {
                "config": config,
                "runtime_dir": getattr(config, "runtime_dir", None),
            },
        )()

    def _client(self) -> BokioClient:
        return BokioClient.from_context(self._context())

    def _payload(self, resolved_config: Mapping[str, Any] | None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        definition = getattr(self, "effect_definition", None) or getattr(
            self, "query_definition", None
        )
        config = getattr(definition, "config", None)
        if isinstance(config, dict):
            payload.update(config)
        if resolved_config:
            payload.update(dict(resolved_config))
        return payload

    def _store(self) -> PreparedActionStore:
        runtime_dir = getattr(self._context(), "runtime_dir", None)
        if runtime_dir is None:
            raise RuntimeError("Bokio provider requires runtime_dir")
        return PreparedActionStore(runtime_dir)


class BokioCompanyQueryProvider(_BokioProviderMixin, QueryProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        del resolved_config
        return {"result": self._client().get_company()}


class BokioChartOfAccountsQueryProvider(_BokioProviderMixin, QueryProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(resolved_config)
        return {"result": self._client().list_accounts(query=_optional(payload.get("query")))}


class BokioJournalEntriesQueryProvider(_BokioProviderMixin, QueryProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(resolved_config)
        return {"result": self._client().list_journal_entries(**_page_args(payload))}


class BokioInvoicesQueryProvider(_BokioProviderMixin, QueryProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(resolved_config)
        return {"result": self._client().list_invoices(**_page_args(payload))}


class BokioSupplierInvoicesQueryProvider(_BokioProviderMixin, QueryProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(resolved_config)
        return {"result": self._client().list_supplier_invoices(**_page_args(payload))}


class BokioUploadDocumentEffectProvider(_BokioProviderMixin, EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(resolved_config)
        return {
            "result": self._client().upload_document(
                file_path=_required(payload.get("file_path"), "file_path"),
                description=_optional(payload.get("description")),
                journal_entry_id=_optional(
                    payload.get("journalEntryId") or payload.get("journal_entry_id")
                ),
            )
        }


class _PrepareActionEffectProvider(_BokioProviderMixin, EffectProviderBase):
    action_kind = ""

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(resolved_config)
        action = self._store().create_action(
            kind=self.action_kind,
            payload=_object(payload.get("payload"), "payload"),
            source=_optional(payload.get("source")) or "workflow",
            note=_optional(payload.get("note")),
        )
        return {"action": action, "prepared_action_id": action["action_id"]}


class BokioPrepareJournalEntryEffectProvider(_PrepareActionEffectProvider):
    action_kind = "journal_entry"


class BokioPrepareSupplierInvoiceEffectProvider(_PrepareActionEffectProvider):
    action_kind = "supplier_invoice"


class BokioPrepareBankPaymentEffectProvider(_PrepareActionEffectProvider):
    action_kind = "bank_payment"


class _CreatePreparedActionEffectProvider(_BokioProviderMixin, EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(resolved_config)
        action_id = _required(
            payload.get("prepared_action_id") or payload.get("action_id"),
            "prepared_action_id",
        )
        return _execute_prepared_action(self._context(), action_id)


class BokioCreateJournalEntryEffectProvider(_CreatePreparedActionEffectProvider):
    pass


class BokioCreateSupplierInvoiceEffectProvider(_CreatePreparedActionEffectProvider):
    pass


class BokioCreateBankPaymentEffectProvider(_CreatePreparedActionEffectProvider):
    pass


def _page_args(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "page": int(payload.get("page") or 1),
        "page_size": int(payload.get("page_size") or payload.get("pageSize") or 25),
        "query": _optional(payload.get("query")),
    }


def _required(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _optional(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return dict(value)


def _field(
    key: str,
    label: str,
    kind: str = "text",
    *,
    required: bool = False,
    bindable: bool = True,
    multiline: bool = False,
    json_value_type: str | None = None,
) -> WorkflowEditorFieldDefinition:
    return WorkflowEditorFieldDefinition(
        key=key,
        label=label,
        kind=kind,
        required=required,
        bindable=bindable,
        multiline=multiline,
        json_value_type=json_value_type,
    )


_PAGE_FIELDS = [
    _field("page", "Page", "number"),
    _field("page_size", "Page size", "number"),
    _field("query", "Query"),
]

_PAYLOAD_FIELD = _field(
    "payload",
    "Payload",
    "json",
    required=True,
    multiline=True,
    json_value_type="object",
)

_CATALOG = [
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:bokio:company",
        label="Bokio company",
        description="Read Bokio company information.",
        fields=[],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:bokio:chart-of-accounts",
        label="Bokio accounts",
        description="Read Bokio chart of accounts.",
        fields=[_field("query", "Query")],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:bokio:journal-entries",
        label="Bokio journal entries",
        description="Read Bokio journal entries.",
        fields=list(_PAGE_FIELDS),
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:bokio:invoices",
        label="Bokio invoices",
        description="Read Bokio invoices.",
        fields=list(_PAGE_FIELDS),
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:bokio:supplier-invoices",
        label="Bokio supplier invoices",
        description="Read Bokio supplier invoices. This Bokio API surface is preview.",
        fields=list(_PAGE_FIELDS),
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:bokio:upload-document",
        label="Bokio upload document",
        description="Upload a host-local document to Bokio.",
        fields=[
            _field("file_path", "File path", required=True),
            _field("description", "Description"),
            _field("journal_entry_id", "Journal entry ID"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:bokio:prepare-journal-entry",
        label="Prepare Bokio journal entry",
        description="Create a local prepared journal entry action for approval.",
        fields=[_PAYLOAD_FIELD, _field("note", "Note")],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:bokio:create-journal-entry",
        label="Create approved Bokio journal entry",
        description="Create a Bokio journal entry from an approved prepared action.",
        fields=[_field("prepared_action_id", "Prepared action ID", required=True)],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:bokio:prepare-supplier-invoice",
        label="Prepare Bokio supplier invoice",
        description="Create a local prepared supplier invoice action for approval.",
        fields=[_PAYLOAD_FIELD, _field("note", "Note")],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:bokio:create-supplier-invoice",
        label="Create approved Bokio supplier invoice",
        description="Create a Bokio supplier invoice from an approved prepared action.",
        fields=[_field("prepared_action_id", "Prepared action ID", required=True)],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:bokio:prepare-bank-payment",
        label="Prepare Bokio bank payment",
        description="Create a local prepared bank payment action for approval.",
        fields=[_PAYLOAD_FIELD, _field("note", "Note")],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:bokio:create-bank-payment",
        label="Create approved Bokio bank payment",
        description="Create a Bokio bank payment. BankID signing remains required in Bokio.",
        fields=[_field("prepared_action_id", "Prepared action ID", required=True)],
    ),
]
