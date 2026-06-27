from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agent_sandbox.admin.service import (
        ConformanceReportRunPayload,
        ConformanceReportsPayload,
        WorkflowFailureClusterReportPayload,
        WorkflowGraphActivityReportPayload,
    )


class _WorkflowReportsAdminServiceHost(Protocol):
    def _read_workflow_graph_activity_report_public_impl(
        self,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        limit: int | None = None,
    ) -> "WorkflowGraphActivityReportPayload": ...
    def _read_workflow_failure_clusters_report_public_impl(
        self,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        limit: int | None = None,
        recent_threshold_seconds: int | None = None,
    ) -> "WorkflowFailureClusterReportPayload": ...
    def _read_conformance_reports_public_impl(self) -> "ConformanceReportsPayload": ...
    def _run_conformance_report_public_impl(
        self, kind: str
    ) -> "ConformanceReportRunPayload": ...


class WorkflowReportsAdminService:
    """Workflow reporting operations extracted from AdminService."""

    def __init__(self, host: _WorkflowReportsAdminServiceHost) -> None:
        self._host = host

    def read_workflow_graph_activity_report(
        self,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        limit: int | None = None,
    ) -> "WorkflowGraphActivityReportPayload":
        return self._host._read_workflow_graph_activity_report_public_impl(
            period_start=period_start, period_end=period_end, limit=limit
        )

    def read_workflow_failure_clusters_report(
        self,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        limit: int | None = None,
        recent_threshold_seconds: int | None = None,
    ) -> "WorkflowFailureClusterReportPayload":
        return self._host._read_workflow_failure_clusters_report_public_impl(
            period_start=period_start,
            period_end=period_end,
            limit=limit,
            recent_threshold_seconds=recent_threshold_seconds,
        )

    def read_conformance_reports(self) -> "ConformanceReportsPayload":
        return self._host._read_conformance_reports_public_impl()

    def run_conformance_report(self, kind: str) -> "ConformanceReportRunPayload":
        return self._host._run_conformance_report_public_impl(kind)
