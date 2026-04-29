from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directory_config import (
    DIRECTORY_CONFIG_DOMAIN_FILENAMES,
    DirectoryConfigResolution,
    DirectoryConfigStore,
    validate_effective_domain_payload,
)
from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.models import DirectoryDetails, DirectoryInfo

T = TypeVar("T")


class AdminDiagnostic(BaseModel):
    code: str
    message: str
    field: str | None = None


class AdminResult(BaseModel, Generic[T]):
    ok: bool
    data: T | None = None
    errors: list[AdminDiagnostic] = Field(default_factory=list)
    validation_diagnostics: list[AdminDiagnostic] = Field(default_factory=list)


class DirectoryTreeNode(BaseModel):
    directory: DirectoryInfo
    config_domains: list[str] = Field(default_factory=list)
    children: list["DirectoryTreeNode"] = Field(default_factory=list)


class DirectoryListPayload(BaseModel):
    parent: DirectoryInfo
    children: list[DirectoryInfo] = Field(default_factory=list)


class DirectoryGetPayload(BaseModel):
    details: DirectoryDetails
    children: list[DirectoryInfo] = Field(default_factory=list)
    config_domains: list[str] = Field(default_factory=list)


class DirectoryConfigPayload(BaseModel):
    directory: DirectoryInfo
    domain: str
    exists: bool
    config: dict[str, Any] | None = None


class DirectoryConfigWritePayload(BaseModel):
    directory: DirectoryInfo
    domain: str
    config: dict[str, Any]


class DirectoryEffectiveConfigPayload(BaseModel):
    resolution: DirectoryConfigResolution


class DirectoryDeletePayload(BaseModel):
    deleted_count: int
    deleted_ids: list[str] = Field(default_factory=list)


class DirectoryAdminService:
    def __init__(
        self,
        *,
        directories: DirectoryService,
        config_store: DirectoryConfigStore,
        gateway_config: GatewayConfig,
    ) -> None:
        self._directories = directories
        self._config_store = config_store
        self._gateway_config = gateway_config

    @classmethod
    def from_store(
        cls,
        *,
        directory_store: DirectoryStore,
        gateway_config: GatewayConfig,
    ) -> "DirectoryAdminService":
        directories = DirectoryService(directory_store)
        config_store = DirectoryConfigStore(directory_store, runtime_dir=gateway_config.runtime_dir)
        config_store.ensure_root()
        return cls(
            directories=directories,
            config_store=config_store,
            gateway_config=gateway_config,
        )

    def tree(self, reference: str = "root") -> AdminResult[DirectoryTreeNode]:
        return self._run(lambda: self._build_tree(reference))

    def list(self, parent: str = "root") -> AdminResult[DirectoryListPayload]:
        return self._run(
            lambda: DirectoryListPayload(
                parent=self._directories.show(parent).directory,
                children=self._directories.list_children(parent=parent),
            )
        )

    def get(self, reference: str) -> AdminResult[DirectoryGetPayload]:
        return self._run(lambda: self._get_payload(reference))

    def create_child(self, *, parent: str, name: str) -> AdminResult[DirectoryGetPayload]:
        if not self._writes_enabled():
            return self._feature_disabled()

        def operation() -> DirectoryGetPayload:
            directory = self._directories.create(parent=parent, name=name)
            return self._get_payload(directory.id)

        return self._run(operation)

    def update(
        self,
        reference: str,
        *,
        new_name: str | None = None,
        new_parent: str | None = None,
    ) -> AdminResult[DirectoryGetPayload]:
        if new_name is None and new_parent is None:
            return self._error(
                "invalid_update",
                "update requires at least one of new_name or new_parent",
            )
        if not self._writes_enabled():
            return self._feature_disabled()

        def operation() -> DirectoryGetPayload:
            directory = self._directories.show(reference).directory
            reference_id = directory.id
            if new_name is not None:
                self._directories.rename(reference_id, new_name=new_name)
            if new_parent is not None:
                self._directories.move(reference_id, new_parent=new_parent)
            return self._get_payload(reference_id)

        return self._run(operation)

    def delete(
        self, reference: str, *, recursive: bool = False
    ) -> AdminResult[DirectoryDeletePayload]:
        if not self._writes_enabled():
            return self._feature_disabled()

        def operation() -> DirectoryDeletePayload:
            directory = self._directories.show(reference).directory
            deleted = self._directories.delete(directory.id, force=recursive)
            self._config_store.delete_directory_tree(directory.path)
            return DirectoryDeletePayload.model_validate(deleted)

        return self._run(operation)

    def read_domain_config(
        self, reference: str, *, domain: str
    ) -> AdminResult[DirectoryConfigPayload]:
        def operation() -> DirectoryConfigPayload:
            directory = self._directories.show(reference).directory
            config = self._config_store.read(directory.id, domain=domain)
            return DirectoryConfigPayload(
                directory=directory,
                domain=domain,
                exists=config is not None,
                config=config,
            )

        return self._run(operation)

    def write_domain_config(
        self,
        reference: str,
        *,
        domain: str,
        payload: dict[str, Any],
    ) -> AdminResult[DirectoryConfigWritePayload]:
        if not self._writes_enabled():
            return self._feature_disabled()

        def operation() -> DirectoryConfigWritePayload:
            directory = self._directories.show(reference).directory
            written = self._config_store.write(directory.id, domain=domain, payload=payload)
            return DirectoryConfigWritePayload(
                directory=directory,
                domain=domain,
                config=written,
            )

        return self._run(operation)

    def compute_effective_config(
        self,
        reference: str,
        *,
        domain: str,
    ) -> AdminResult[DirectoryEffectiveConfigPayload]:
        def operation() -> DirectoryEffectiveConfigPayload:
            base_config = (
                self._gateway_config.model_dump(mode="json") if domain == "gateway" else None
            )
            resolution = self._config_resolver().resolve_domain(
                reference,
                domain=domain,
                base_config=base_config,
                validator=lambda candidate: validate_effective_domain_payload(domain, candidate),
            )
            return DirectoryEffectiveConfigPayload(resolution=resolution)

        return self._run(operation, include_validation=True)

    def _build_tree(self, reference: str) -> DirectoryTreeNode:
        directory = self._directories.show(reference).directory
        children = [
            self._build_tree(child.id)
            for child in self._directories.list_children(parent=directory.id)
        ]
        return DirectoryTreeNode(
            directory=directory,
            config_domains=self._config_store.list_domains(directory.id),
            children=children,
        )

    def _get_payload(self, reference: str) -> DirectoryGetPayload:
        details = self._directories.show(reference)
        return DirectoryGetPayload(
            details=details,
            children=self._directories.list_children(parent=details.directory.id),
            config_domains=self._config_store.list_domains(details.directory.id),
        )

    def _config_resolver(self):

        from agent_sandbox.directory_config import DirectoryConfigResolver

        return DirectoryConfigResolver(
            self._directories,
            base_gateway_config=self._gateway_config,
            config_store=self._config_store,
        )

    def _writes_enabled(self) -> bool:
        return self._gateway_config.enable_directory_scoped_config

    def _run(
        self,
        operation,
        *,
        include_validation: bool = False,
    ) -> AdminResult[Any]:
        try:
            data = operation()
        except ValueError as exc:
            return self._error("validation_error", str(exc))

        validation_diagnostics: list[AdminDiagnostic] = []
        if include_validation and hasattr(data, "resolution"):
            resolution = getattr(data, "resolution")
            if isinstance(resolution, DirectoryConfigResolution):
                validation_diagnostics = [
                    AdminDiagnostic(code="validation_error", message=message)
                    for message in resolution.validation_errors
                ]
        return AdminResult(ok=True, data=data, validation_diagnostics=validation_diagnostics)

    @staticmethod
    def _error(code: str, message: str, *, field: str | None = None) -> AdminResult[Any]:
        return AdminResult(
            ok=False,
            errors=[AdminDiagnostic(code=code, message=message, field=field)],
        )

    @classmethod
    def _feature_disabled(cls) -> AdminResult[Any]:
        return cls._error(
            "feature_disabled",
            "directory-scoped config is disabled; set enable_directory_scoped_config=true to enable edit surfaces",
        )


DirectoryTreeNode.model_rebuild()
