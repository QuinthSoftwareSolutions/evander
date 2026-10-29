from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import logging
import time

from evander.admin.services.managed_git_runtime_service import ManagedGitMaintenancePort
from evander.signal_cleanup import (
    install_signal_cleanup_handlers,
    restore_signal_handlers,
)


LOGGER = logging.getLogger(__name__)


class ManagedGitWorkerLifecycle:
    """Owns managed-Git worker reconciliation and process cleanup."""

    def __init__(
        self,
        *,
        reconcile_interval_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
        logger: logging.Logger = LOGGER,
    ) -> None:
        if reconcile_interval_seconds < 0:
            raise ValueError("reconcile_interval_seconds cannot be negative")
        self._reconcile_interval_seconds = reconcile_interval_seconds
        self._monotonic = monotonic
        self._logger = logger
        self._maintenance: ManagedGitMaintenancePort | None = None
        self._cleanup_ports: list[ManagedGitMaintenancePort] = []
        self._last_reconcile_at = 0.0

    def configure(self, maintenance: ManagedGitMaintenancePort) -> None:
        if all(existing is not maintenance for existing in self._cleanup_ports):
            self._cleanup_ports.append(maintenance)
        self._maintenance = maintenance

    def reconcile(self, *, force: bool = False, strict: bool = False) -> None:
        maintenance = self._maintenance
        if maintenance is None:
            return
        now = self._monotonic()
        if not force and now - self._last_reconcile_at < self._reconcile_interval_seconds:
            return
        self._last_reconcile_at = now
        try:
            maintenance.reconcile_stale_worktrees()
        except Exception:
            if strict:
                raise
            self._logger.exception("Managed Git worktree reconciliation failed")

    def cleanup_active_worktrees(self) -> None:
        for maintenance in list(self._cleanup_ports):
            try:
                maintenance.cleanup_active_worktrees()
            except Exception:
                self._logger.exception("Managed Git active worktree cleanup failed")

    @contextmanager
    def cleanup_on_exit(self) -> Iterator[None]:
        old_handlers = (
            install_signal_cleanup_handlers(self.cleanup_active_worktrees)
            if self._cleanup_ports
            else {}
        )
        try:
            yield
        finally:
            try:
                self.cleanup_active_worktrees()
            finally:
                restore_signal_handlers(old_handlers)
