"""VM and session visibility helpers for gateway tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_sandbox.admin.service import AdminService
from agent_sandbox.policy import PolicyError
from agent_sandbox.runtime import RuntimeContext


def _visible_vm_summaries_for_session(
    context: RuntimeContext,
    session: Any,
    *,
    vm_user: str,
    admin_service: AdminService | None = None,
) -> list[Any]:
    service = admin_service or AdminService(context.config.runtime_dir)
    return service.vm_agent_admin.list_vms(
        include_inactive=True,
        vm_user=vm_user,
        directory_path=session.directory_path,
        direct_only=True,
    )


def _require_vm_visible_in_session_directory(
    context: RuntimeContext,
    session: Any,
    *,
    vm_name: str,
    vm_user: str,
    admin_service: AdminService | None = None,
) -> Any:
    summaries = _visible_vm_summaries_for_session(
        context,
        session,
        vm_user=vm_user,
        admin_service=admin_service,
    )
    summary = next((item for item in summaries if item.vm_name == vm_name), None)
    if summary is None:
        raise PolicyError(
            f"VM '{vm_name}' is outside the session directory scope '{session.directory_path}'."
        )
    return summary


def _require_target_session_visible_within_directory(
    context: RuntimeContext,
    source_session: Any,
    *,
    target_session_id: str,
    vm_user: str,
    admin_service: AdminService | None = None,
) -> Any:
    target_session = context.policy.get_session(target_session_id)
    if target_session.directory_path == source_session.directory_path:
        return target_session

    target_vm_id = str(getattr(target_session, "vm_id", "") or "").strip()
    if not target_vm_id:
        raise PolicyError(
            f"Session '{target_session_id}' is outside the session directory scope "
            f"'{source_session.directory_path}'."
        )

    visible_vms = _visible_vm_summaries_for_session(
        context,
        source_session,
        vm_user=vm_user,
        admin_service=admin_service,
    )
    if any(target_session_id in summary.session_ids for summary in visible_vms):
        return target_session

    raise PolicyError(
        f"Session '{target_session_id}' is outside the session directory scope "
        f"'{source_session.directory_path}'."
    )


@dataclass(frozen=True)
class VisibilityHelpers:
    context: RuntimeContext
    admin_service_factory: Callable[[], AdminService]

    def admin_service(self) -> AdminService:
        return self.admin_service_factory()

    def require_vm_visible_in_session_directory(
        self,
        context: RuntimeContext,
        session: Any,
        **kwargs: Any,
    ) -> Any:
        kwargs.setdefault("admin_service", self.admin_service())
        return _require_vm_visible_in_session_directory(context, session, **kwargs)

    def require_target_session_visible_within_directory(
        self,
        context: RuntimeContext,
        source_session: Any,
        **kwargs: Any,
    ) -> Any:
        kwargs.setdefault("admin_service", self.admin_service())
        return _require_target_session_visible_within_directory(
            context,
            source_session,
            **kwargs,
        )
