from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from evander.provider_config import (
    EffectProviderConfig,
    GateProviderConfig,
    QueryProviderConfig,
    TriggerProviderConfig,
)
from evander.provider_runtime.contracts import ProviderRuntimePorts
from evander.repositories.skills_library import normalize_skill_names


class AdminRuntimeProviderAdapter:
    """Narrow provider-runtime adapter over a host runtime object."""

    _CONDITIONALLY_BACKED_METHODS = {
        "validate_managed_codex_skills": "_managed_codex_skill_validator",
        "resolve_container_codex_skill_files": "_container_codex_skill_file_resolver",
        "list_model_usage_events": "_model_usage_events_lister",
        "model_pricing_catalog": "_model_pricing_catalog_loader",
        "sync_added_discord_session_cursors": "_sync_added_discord_session_cursors_delegate",
        "delete_admin_agent": "_delete_admin_agent_delegate",
    }

    def __init__(self, admin_runtime: object) -> None:
        self._admin_runtime = admin_runtime
        self.runtime = getattr(admin_runtime, "runtime", None)
        self.config = getattr(admin_runtime, "config", None)
        project_dir = getattr(admin_runtime, "project_dir", None)
        if project_dir is not None:
            self.project_dir = project_dir
        self.vm_agent_admin = getattr(admin_runtime, "vm_agent_admin", None)
        self.issue_git_admin = getattr(admin_runtime, "issue_git_admin", None)
        self.managed_git_admin = getattr(admin_runtime, "managed_git_admin", None)
        self.git_submission_admin = getattr(admin_runtime, "git_submission_admin", None)
        self.repository_index_admin = getattr(admin_runtime, "repository_index_admin", None)
        self.connector_service = getattr(admin_runtime, "connector_service", None)
        self.security_scan_service = getattr(admin_runtime, "security_scan_service", None)
        self.event_schemas = getattr(getattr(admin_runtime, "workflow_config_admin", None), "event_schemas", None)

    def __getattribute__(self, name: str) -> Any:
        conditional_methods = object.__getattribute__(self, "_CONDITIONALLY_BACKED_METHODS")
        resolver_name = conditional_methods.get(name)
        if resolver_name is not None:
            resolver = object.__getattribute__(self, resolver_name)
            if resolver() is None:
                raise AttributeError(name)
        return object.__getattribute__(self, name)

    def _vm_agent_admin(self) -> object:
        return self.vm_agent_admin or self._admin_runtime

    def _issue_git_admin(self) -> object:
        return self.issue_git_admin or self._admin_runtime

    def _operations_admin(self) -> object:
        return getattr(self._admin_runtime, "operations_admin", self._admin_runtime)

    def _delegate_method(self, attr_name: str, *owners: object | None) -> Callable[..., Any] | None:
        for owner in owners:
            if owner is None or owner is self:
                continue
            method = getattr(owner, attr_name, None)
            if callable(method):
                return method
        return None

    def _vm_agent_method(self, attr_name: str) -> Callable[..., Any] | None:
        return self._delegate_method(attr_name, self.vm_agent_admin, self._admin_runtime)

    def _issue_git_method(self, attr_name: str) -> Callable[..., Any] | None:
        return self._delegate_method(attr_name, self.issue_git_admin, self._admin_runtime)

    def _managed_git_method(self, attr_name: str) -> Callable[..., Any] | None:
        return self._delegate_method(attr_name, self.managed_git_admin, self._admin_runtime)

    def _call_required(
        self,
        method: Callable[..., Any] | None,
        attr_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if method is None:
            raise AttributeError(attr_name)
        return method(*args, **kwargs)

    def _managed_codex_skill_validator(self) -> Callable[..., Any] | None:
        return self._delegate_method(
            "validate_managed_codex_skills",
            getattr(self._admin_runtime, "skill_admin", None),
            self.vm_agent_admin,
            self._admin_runtime,
        )

    def _container_codex_skill_file_resolver(self) -> Callable[..., Any] | None:
        return self._delegate_method(
            "resolve_container_codex_skill_files",
            getattr(self._admin_runtime, "skill_admin", None),
            self.vm_agent_admin,
            self._admin_runtime,
        )

    def _model_usage_events_lister(self) -> Callable[..., Any] | None:
        return self._delegate_method(
            "list_model_usage_events",
            getattr(self._admin_runtime, "model_usage_admin", None),
            self._admin_runtime,
        )

    def _model_pricing_catalog_loader(self) -> Callable[..., Any] | None:
        return self._delegate_method(
            "model_pricing_catalog",
            getattr(self._admin_runtime, "model_usage_admin", None),
            self._admin_runtime,
        )

    def _sync_added_discord_session_cursors_delegate(self) -> Callable[..., Any] | None:
        return self._vm_agent_method("sync_added_discord_session_cursors")

    def _delete_admin_agent_delegate(self) -> Callable[..., Any] | None:
        return self._vm_agent_method("delete_admin_agent")

    def create_vm(self, **kwargs: Any) -> Any:
        return self._call_required(self._vm_agent_method("create_vm"), "create_vm", **kwargs)

    def resolve_vm_defaults(self, directory_path: str) -> Any:
        return self._call_required(
            self._vm_agent_method("resolve_vm_defaults"),
            "resolve_vm_defaults",
            directory_path,
        )

    def clone_repo_into_vm(self, **kwargs: Any) -> Any:
        return self._call_required(
            self._vm_agent_method("clone_repo_into_vm"),
            "clone_repo_into_vm",
            **kwargs,
        )

    def list_managed_git_access(self, *args: Any, **kwargs: Any) -> Any:
        return self._call_required(
            self._vm_agent_method("list_managed_git_access"),
            "list_managed_git_access",
            *args,
            **kwargs,
        )

    def grant_managed_git_access(self, *args: Any, **kwargs: Any) -> Any:
        return self._call_required(
            self._vm_agent_method("grant_managed_git_access"),
            "grant_managed_git_access",
            *args,
            **kwargs,
        )

    def revoke_managed_git_access(self, *args: Any, **kwargs: Any) -> Any:
        return self._call_required(
            self._vm_agent_method("revoke_managed_git_access"),
            "revoke_managed_git_access",
            *args,
            **kwargs,
        )

    def ensure_managed_git_repository(self, **kwargs: Any) -> Any:
        return self._call_required(
            self._managed_git_method("register_repository"),
            "ensure_managed_git_repository",
            **kwargs,
        )

    def get_managed_git_repository(self, *args: Any, **kwargs: Any) -> Any:
        return self._call_required(
            self._managed_git_method("get_repository"),
            "get_managed_git_repository",
            *args,
            **kwargs,
        )

    def resolve_managed_git_clone(self, **kwargs: Any) -> Any:
        return self._call_required(
            self._managed_git_method("clone_info"),
            "resolve_managed_git_clone",
            **kwargs,
        )

    def refresh_managed_git_repository(self, *args: Any, **kwargs: Any) -> Any:
        return self._call_required(
            self._managed_git_method("refresh_repository"),
            "refresh_managed_git_repository",
            *args,
            **kwargs,
        )

    def refresh_managed_git_repository_if_branch_changed(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        return self._call_required(
            self._managed_git_method("refresh_repository_if_branch_changed"),
            "refresh_managed_git_repository_if_branch_changed",
            *args,
            **kwargs,
        )

    def resolve_managed_git_branch_commit(self, *args: Any, **kwargs: Any) -> Any:
        return self._call_required(
            self._managed_git_method("resolve_branch_commit"),
            "resolve_managed_git_branch_commit",
            *args,
            **kwargs,
        )

    def resolve_managed_git_merge_base(self, *args: Any, **kwargs: Any) -> Any:
        return self._call_required(
            self._managed_git_method("resolve_merge_base"),
            "resolve_managed_git_merge_base",
            *args,
            **kwargs,
        )

    def verify_managed_git_reference_repo_commit_transition(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        return self._call_required(
            self._managed_git_method("verify_reference_repo_commit_transition"),
            "verify_managed_git_reference_repo_commit_transition",
            *args,
            **kwargs,
        )

    def bind_managed_git_repository(self, **kwargs: Any) -> Any:
        return self._call_required(
            self._managed_git_method("bind"),
            "bind_managed_git_repository",
            **kwargs,
        )

    def unbind_all_managed_git_repositories(self, **kwargs: Any) -> Any:
        return self._call_required(
            self._managed_git_method("unbind_all"),
            "unbind_all_managed_git_repositories",
            **kwargs,
        )

    def unbind_managed_git_repository(self, **kwargs: Any) -> Any:
        return self._call_required(
            self._managed_git_method("unbind"),
            "unbind_managed_git_repository",
            **kwargs,
        )

    def unbind_managed_git_repositories(self, **kwargs: Any) -> Any:
        return self._call_required(
            self._managed_git_method("unbind_all"),
            "unbind_managed_git_repositories",
            **kwargs,
        )

    def reconcile_managed_git_worktrees(self, **kwargs: Any) -> Any:
        return self._call_required(
            self._managed_git_method("reconcile_stale_worktrees"),
            "reconcile_managed_git_worktrees",
            **kwargs,
        )

    def bootstrap_python_in_vm(self, **kwargs: Any) -> Any:
        return self._call_required(
            self._vm_agent_method("bootstrap_python_in_vm"),
            "bootstrap_python_in_vm",
            **kwargs,
        )

    def bootstrap_frontend_in_vm(self, **kwargs: Any) -> Any:
        return self._call_required(
            self._vm_agent_method("bootstrap_frontend_in_vm"),
            "bootstrap_frontend_in_vm",
            **kwargs,
        )

    def resume_agent(self, **kwargs: Any) -> Any:
        return self._call_required(self._vm_agent_method("resume_agent"), "resume_agent", **kwargs)

    def enqueue_agent_prompt(self, **kwargs: Any) -> Any:
        return self._call_required(
            self._vm_agent_method("enqueue_agent_prompt"),
            "enqueue_agent_prompt",
            **kwargs,
        )

    def dispatch_agent_prompt(self, **kwargs: Any) -> Any:
        """Bypass queue-aware entry points after an effect has won admission."""
        return self._call_required(self._vm_agent_method("resume_agent"), "resume_agent", **kwargs)

    def start_vm(self, target: str) -> Any:
        return self._call_required(self._vm_agent_method("start_vm"), "start_vm", target)

    def shutdown_vm(self, target: str, *, timeout_seconds: int = 60) -> Any:
        return self._call_required(
            self._vm_agent_method("shutdown_vm"),
            "shutdown_vm",
            target,
            timeout_seconds=timeout_seconds,
        )

    def list_vms(self, **kwargs: Any) -> list[Any]:
        return self._call_required(self._vm_agent_method("list_vms"), "list_vms", **kwargs)

    def vm_exec(self, **kwargs: Any) -> Mapping[str, Any]:
        return self._call_required(self._vm_agent_method("vm_exec"), "vm_exec", **kwargs)

    def vm_copy_files(self, **kwargs: Any) -> Mapping[str, Any]:
        return self._call_required(
            self._vm_agent_method("vm_copy_files"),
            "vm_copy_files",
            **kwargs,
        )

    def vm_delete_files(self, **kwargs: Any) -> Mapping[str, Any]:
        return self._call_required(
            self._vm_agent_method("vm_delete_files"),
            "vm_delete_files",
            **kwargs,
        )

    def read_all_sessions(self) -> Mapping[str, Any]:
        runtime = self.runtime
        session_repository = getattr(runtime, "session_repository", None)
        if session_repository is not None:
            return {session.session_id: session for session in session_repository.list_sessions()}
        return {}

    def update_issue(self, issue_id: str, **kwargs: Any) -> Any:
        return self._call_required(
            self._issue_git_method("update_issue"),
            "update_issue",
            issue_id,
            **kwargs,
        )

    def update_issue_metadata(self, **kwargs: Any) -> Any:
        return self._call_required(
            self._issue_git_method("update_issue_metadata"),
            "update_issue_metadata",
            **kwargs,
        )

    def session_reset_context(self, **kwargs: Any) -> Any:
        service = getattr(self._admin_runtime, "conversation_context_admin", None)
        return self._call_required(
            getattr(service, "reset_context", None), "session_reset_context", **kwargs
        )

    def session_update_metadata(self, *, target: str, metadata: dict[str, Any]) -> Any:
        return self._call_required(
            self._issue_git_method("session_update_metadata"),
            "session_update_metadata",
            target=target,
            metadata=metadata,
        )

    def session_set_image_generation_enabled(self, *, target: str, enabled: bool) -> Any:
        return self._call_required(
            self._issue_git_method("session_set_image_generation_enabled"),
            "session_set_image_generation_enabled",
            target=target,
            enabled=enabled,
        )

    def promote_agent_branch(self, **kwargs: Any) -> Any:
        return self._call_required(
            self._managed_git_method("promote_agent_branch"),
            "promote_agent_branch",
            **kwargs,
        )

    def push_branch(self, **kwargs: Any) -> Any:
        return self._call_required(self._managed_git_method("push_branch"), "push_branch", **kwargs)

    def register_git_submission_checkout(self, **kwargs: Any) -> str | None:
        method = getattr(self.git_submission_admin, "register_checkout", None)
        return method(**kwargs) if callable(method) else None

    def pr_create(self, **kwargs: Any) -> Any:
        return self._call_required(self._managed_git_method("pr_create"), "pr_create", **kwargs)

    def git_pr_note_query(self, **kwargs: Any) -> Any:
        return self._call_required(
            self._managed_git_method("read_pr_note"),
            "git_pr_note_query",
            **kwargs,
        )

    def get_issue(self, **kwargs: Any) -> Any:
        return self._call_required(self._issue_git_method("get_issue"), "get_issue", **kwargs)

    def list_issues(self, **kwargs: Any) -> Any:
        return self._call_required(self._issue_git_method("list_issues"), "list_issues", **kwargs)

    def session_context_query(self, **kwargs: Any) -> Any:
        return self._call_required(
            self._issue_git_method("session_context_query"),
            "session_context_query",
            **kwargs,
        )

    def read_session_artifact_text(self, **kwargs: Any) -> Any:
        return self._call_required(
            self._vm_agent_method("read_session_artifact_text"),
            "read_session_artifact_text",
            **kwargs,
        )

    def sync_added_discord_session_cursors(self, *args: Any, **kwargs: Any) -> None:
        return self._call_required(
            self._sync_added_discord_session_cursors_delegate(),
            "sync_added_discord_session_cursors",
            *args,
            **kwargs,
        )

    def validate_managed_codex_skills(
        self, skill_names: list[str] | tuple[str, ...] | None
    ) -> list[str]:
        validator = self._managed_codex_skill_validator()
        if validator is None:
            return normalize_skill_names(skill_names)
        return validator(normalize_skill_names(skill_names))

    def resolve_container_codex_skill_files(
        self, skill_names: list[str] | tuple[str, ...] | None
    ) -> list[dict[str, Any]]:
        resolver = self._container_codex_skill_file_resolver()
        if resolver is None:
            raise AttributeError("resolve_container_codex_skill_files")
        return resolver(skill_names)

    def read_codex_subscription_quota(self, **kwargs: Any) -> Any:
        service = getattr(self._admin_runtime, "codex_auth_admin", None)
        return self._call_required(
            self._delegate_method("profile_quota", service),
            "read_codex_subscription_quota",
            **kwargs,
        )

    def resolve_website_session_profile(self, **kwargs: Any) -> Any:
        service = getattr(self._admin_runtime, "website_session_admin", None)
        return self._call_required(
            self._delegate_method("resolve_profile", service),
            "resolve_website_session_profile",
            **kwargs,
        )

    def ensure_website_session_profile(self, **kwargs: Any) -> Any:
        service = getattr(self._admin_runtime, "website_session_admin", None)
        return self._call_required(
            self._delegate_method("ensure_profile", service),
            "ensure_website_session_profile",
            **kwargs,
        )

    def list_model_usage_events(self, **kwargs: Any) -> Any:
        lister = self._model_usage_events_lister()
        if lister is None:
            raise AttributeError("list_model_usage_events")
        return lister(**kwargs)

    def model_pricing_catalog(self) -> Any:
        return self._call_required(
            self._model_pricing_catalog_loader(),
            "model_pricing_catalog",
        )

    def destroy_vm(self, target: str) -> Any:
        return self._call_required(self._vm_agent_method("destroy_vm"), "destroy_vm", target)

    def delete_admin_agent(self, agent_id: str, *, backend: str | None = None) -> Any:
        return self._call_required(
            self._delete_admin_agent_delegate(),
            "delete_admin_agent",
            agent_id,
            backend=backend,
        )

    def read_assistant_settings(self) -> dict[str, object]:
        return self._operations_admin().read_assistant_settings()

    def _resolve_issue_setup(self, directory_path: str, *, root_dir: Any) -> Any:
        return self._vm_agent_admin()._resolve_issue_setup(directory_path, root_dir=root_dir)


class GateProviderAdapter:
    """Narrow gate-runtime adapter over a host runtime object."""

    def __init__(self, admin_runtime: object) -> None:
        self._admin_runtime = admin_runtime

    def list_vms(self, **kwargs: Any) -> list[Any]:
        vm_agent_admin = getattr(self._admin_runtime, "vm_agent_admin", self._admin_runtime)
        return vm_agent_admin.list_vms(**kwargs)


def build_provider_runtime_ports(
    *,
    provider_runtime: object | None = None,
    admin_service: object | None = None,
    admin_adapter: object | None = None,
    gate_adapter: object | None = None,
) -> ProviderRuntimePorts:
    if admin_adapter is None and admin_service is not None:
        admin_adapter = AdminRuntimeProviderAdapter(admin_service)
    if gate_adapter is None and admin_service is not None:
        gate_adapter = GateProviderAdapter(admin_service)
    provider_metadata = _extract_mapping_attr(provider_runtime, "metadata")
    return ProviderRuntimePorts(
        vm_creator=_pick(provider_runtime, "vm_creator", admin_adapter),
        vm_repo_cloner=_pick(provider_runtime, "vm_repo_cloner", admin_adapter),
        vm_python_bootstrapper=_pick(provider_runtime, "vm_python_bootstrapper", admin_adapter),
        vm_frontend_bootstrapper=_pick(provider_runtime, "vm_frontend_bootstrapper", admin_adapter),
        agent_resumer=_pick(provider_runtime, "agent_resumer", admin_adapter),
        vm_power_controller=_pick(provider_runtime, "vm_power_controller", admin_adapter),
        vm_inventory=_pick(provider_runtime, "vm_inventory", admin_adapter),
        gate_vm_inventory=_pick(provider_runtime, "gate_vm_inventory", gate_adapter, admin_adapter),
        vm_executor=_pick(provider_runtime, "vm_executor", admin_adapter),
        vm_file_copier=_pick(provider_runtime, "vm_file_copier", admin_adapter),
        vm_file_deleter=_pick(provider_runtime, "vm_file_deleter", admin_adapter),
        session_lookup=_pick(provider_runtime, "session_lookup", admin_adapter),
        issue_updater=_pick(provider_runtime, "issue_updater", admin_adapter),
        session_metadata_updater=_pick(provider_runtime, "session_metadata_updater", admin_adapter),
        session_context_resetter=_pick(provider_runtime, "session_context_resetter", admin_adapter),
        session_image_generation_policy_updater=_pick(
            provider_runtime,
            "session_image_generation_policy_updater",
            admin_adapter,
        ),
        git_branch_promoter=_pick(provider_runtime, "git_branch_promoter", admin_adapter),
        pull_request_creator=_pick(provider_runtime, "pull_request_creator", admin_adapter),
        git_merge_base_reader=_pick(provider_runtime, "git_merge_base_reader", admin_adapter),
        git_pr_note_reader=_pick(provider_runtime, "git_pr_note_reader", admin_adapter),
        issue_reader=_pick(provider_runtime, "issue_reader", admin_adapter),
        session_context_reader=_pick(provider_runtime, "session_context_reader", admin_adapter),
        session_artifact_reader=_pick(provider_runtime, "session_artifact_reader", admin_adapter),
        admin_service=_pick(provider_runtime, "admin_service", admin_service, admin_adapter),
        security_scan_service=_pick(provider_runtime, "security_scan_service", getattr(admin_adapter, "security_scan_service", None)),
        metadata=provider_metadata,
    )


def trigger_provider_config_blob(config: TriggerProviderConfig) -> dict[str, Any]:
    return config.model_dump(mode="json")


def effect_provider_config_blob(config: EffectProviderConfig) -> dict[str, Any]:
    return config.model_dump(mode="json")


def query_provider_config_blob(config: QueryProviderConfig) -> dict[str, Any]:
    return config.model_dump(mode="json")


def gate_provider_config_blob(config: GateProviderConfig) -> dict[str, Any]:
    return config.model_dump(mode="json")


def _pick(source: object | None, attr_name: str, *fallbacks: object | None) -> object | None:
    value = getattr(source, attr_name, None) if source is not None else None
    if value is not None:
        return value
    for fallback in fallbacks:
        if fallback is None:
            continue
        return fallback
    return None


def _extract_mapping_attr(source: object | None, attr_name: str) -> dict[str, Any]:
    value = getattr(source, attr_name, None) if source is not None else None
    if isinstance(value, Mapping):
        return dict(value)
    return {}
