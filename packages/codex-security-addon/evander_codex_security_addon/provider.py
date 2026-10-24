"""Thin adapter; source inspection and Codex execution always happen in the session."""

from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Mapping

import httpx

from evander.codex_auth import (
    CodexAuthMode,
    effective_codex_auth_mode,
    effective_subscription_profile_id,
)
from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.effects.providers.agent_session import (
    container_backend,
    container_worker_image_for_session,
    container_worker_resource_limits_for_session,
    effective_config_for_session,
    execution_backend_for_session,
    runtime_session_repository,
)
from evander.events.provider_models import AgentExecEffectDefinition
from evander.execution import ExecutionBackendKind, ExecutionSpec, ExecutionStatusKind
from evander.execution.container_worker_client import (
    ContainerWorkerClient,
    ContainerWorkerClientError,
)
from evander.security_scans import (
    SecurityScanHandle,
    SecurityScanResult,
    SecurityScanRetryableError,
)
from evander.published_artifacts import PublishedArtifactStore
from evander.effects.providers.artifacts import ArtifactPublishArtifactsEffectProvider
from evander.events.provider_models import ArtifactPublishArtifactsEffectDefinition
from .report_files import public_report_path

RUNNER = "/opt/evander/codex-security/runner.py"


class CodexSecurityScanProvider:
    coverage_policies = frozenset({"provider", "all_tracked_files"})
    supports_subscription_handoff = True
    supports_batch_text_limit = True
    supports_review_parallelism = True

    @staticmethod
    def describe(**kwargs):
        from .coverage_provider import describe

        return describe(**kwargs)

    @staticmethod
    def maintenance_candidates(runtime_dir):
        from pathlib import Path
        from .coverage import CoverageStore

        if not (Path(runtime_dir) / "codex-security-coverage/journal.sqlite3").is_file():
            return []
        return [(s["scan_id"], s["directory_path"]) for s in CoverageStore(runtime_dir).expired()]

    def maintain(self, runtime):
        from .coverage_provider import CoverageController

        return CoverageController(self, runtime).prune()

    def _config(self, runtime, session_id):
        config = effective_config_for_session(runtime, session_id)
        session = runtime_session_repository(runtime, config).get_session(session_id)
        if session is None:
            raise ValueError(f"Unknown session: {session_id}")
        return config, session

    def _command(self, runtime, session_id, args):
        provider = AgentExecEffectProvider()
        provider.initialize(
            AgentExecEffectDefinition(
                id="security-scan-control",
                display_name="Security scan control",
                type="effect:agent:exec",
                session_id=session_id,
                command=shlex.join(["/usr/bin/python3", RUNNER, *args]),
                timeout_seconds=180 if args[0] == "coverage-inventory" else 30,
                check=False,
            ),
            runtime,
        )
        try:
            result = provider.execute()
        except ContainerWorkerClientError as exc:
            if isinstance(exc.__cause__, httpx.TransportError):
                raise SecurityScanRetryableError("Runner response was lost") from exc
            raise
        except subprocess.TimeoutExpired as exc:
            raise SecurityScanRetryableError("Runner command response was lost") from exc
        if not result.get("success"):
            if result.get("exit_code") == 255:
                raise SecurityScanRetryableError("Runner connection was interrupted")
            raise RuntimeError(result.get("stderr") or "Security scan runner command failed")
        try:
            return json.loads(result["stdout"])
        except (KeyError, ValueError) as exc:
            if args[0] == "launch":
                raise SecurityScanRetryableError("Launch receipt was lost") from exc
            raise ValueError("Security scan runner did not return a JSON receipt") from exc

    def start(self, *, scan_id, request, runtime):
        if request.coverage_policy == "all_tracked_files":
            from .coverage_provider import CoverageController

            return CoverageController(self, runtime).start(scan_id, request)
        return self._start_raw(scan_id=scan_id, request=request, runtime=runtime)

    def _start_raw(self, *, scan_id, request, runtime, payload=None, frozen_route=None):
        config, session = self._config(runtime, request.session_id)
        if frozen_route is not None:
            from .coverage_provider import CoverageController

            if CoverageController(self, runtime).route(request) != frozen_route:
                raise ValueError("Scanner access configuration changed before launch")
        payload = payload or {**request.model_dump(), "scan_id": scan_id}
        backend_kind = (
            "container_worker_vm"
            if frozen_route is not None
            else execution_backend_for_session(session, fallback_config=config)
        )
        if backend_kind == "libvirt_vm":
            self._command(runtime, request.session_id, ["launch", json.dumps(payload)])
            return SecurityScanHandle(
                execution_id=scan_id,
                metadata={
                    "backend": backend_kind,
                    "session_id": request.session_id,
                    "scan_id": scan_id,
                },
            )
        backend = container_backend(runtime, config)
        runtime_spec = None
        metadata = getattr(session, "metadata", {})
        route = (
            metadata.get("model_provider_mode") if isinstance(metadata, Mapping) else None
        ) or config.model_provider_mode
        auth_mode = effective_codex_auth_mode(session=session, config=config)
        if frozen_route is not None:
            route = frozen_route["model_provider_mode"]
            auth_mode = CodexAuthMode(frozen_route["auth_mode"])
        if route == "openai_public" and auth_mode == CodexAuthMode.CHATGPT_SUBSCRIPTION:
            from evander.codex_auth import CodexRuntimeSpec

            runtime_spec = CodexRuntimeSpec(
                profile_id="codex-security",
                subscription_profile_id=(
                    frozen_route["subscription_profile_id"]
                    if frozen_route
                    else effective_subscription_profile_id(session=session)
                ),
                cyber_access_program=(
                    frozen_route["cyber_access_program"]
                    if frozen_route
                    else getattr(session, "cyber_access_program", None)
                ),
            )
        prepared = backend.prepare(
            ExecutionSpec(
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                target_name="scan-" + scan_id,
                session_id=request.session_id,
                directory_path=session.directory_path,
                image=frozen_route["image"]
                if frozen_route
                else container_worker_image_for_session(session, config),
                resource_limits=container_worker_resource_limits_for_session(session),
                command=["/usr/bin/python3", RUNNER, "run", json.dumps(payload)],
                codex_runtime=runtime_spec,
                metadata={
                    "operation_kind": "security_scan",
                    "scan_id": scan_id,
                    "idempotent_start": True,
                    "private_diagnostic_paths": [
                        f"security-scans/{scan_id}/{name}"
                        for name in (
                            "diagnostics",
                            "execution.json",
                            "scan.log",
                            "launcher.log",
                            "request.json",
                            "target.json",
                            "process.json",
                            "result.json",
                            "snapshot-validation.json",
                            "codex-home/sessions",
                            "codex-home/archived_sessions",
                            "codex-home/log",
                            "state/codex-home/sessions",
                            "state/codex-home/archived_sessions",
                            "state/codex-home/log",
                        )
                    ],
                },
            )
        )
        try:
            handle = backend.start(prepared)
        except ContainerWorkerClientError as exc:
            if isinstance(exc.__cause__, httpx.TransportError):
                raise SecurityScanRetryableError("Launch response was lost") from exc
            raise
        except (httpx.TransportError, TimeoutError, ConnectionError) as exc:
            raise SecurityScanRetryableError("Launch response was lost") from exc
        return SecurityScanHandle(
            execution_id=handle.execution_id,
            metadata={
                "backend": backend_kind,
                "session_id": request.session_id,
                "scan_id": scan_id,
            },
        )

    def poll(self, *, handle, runtime):
        data = handle.metadata
        if data.get("coverage_policy") == "all_tracked_files":
            from .coverage_provider import CoverageController

            return CoverageController(self, runtime).poll(handle)
        backend = None
        stopped = False
        if data["backend"] == "container_worker_vm":
            config, _ = self._config(runtime, data["session_id"])
            backend = container_backend(runtime, config)
            stopped = backend.inspect(handle.execution_id).status in {
                ExecutionStatusKind.FAILED,
                ExecutionStatusKind.STOPPED,
                ExecutionStatusKind.DESTROYED,
            }
        action = "status-live" if data["backend"] == "libvirt_vm" else "status"
        try:
            receipt = self._command(runtime, data["session_id"], [action, data["scan_id"]])
        except SecurityScanRetryableError:
            raise
        except RuntimeError as exc:
            if not stopped:
                raise
            # A missing/broken runtime may exit before it can write any scan files.
            backend.destroy(handle.execution_id)
            return SecurityScanResult(status="failed", error=str(exc)[:2000])
        if receipt.get("status") != "running":
            result = SecurityScanResult.model_validate(receipt)
        elif stopped:
            result = SecurityScanResult(
                status="failed", error="Scan process exited without a sealed result"
            )
        else:
            return SecurityScanResult(status="running")
        result = self._preserve_result(result, handle, runtime)
        if backend is not None:
            backend.destroy(handle.execution_id)
        return result

    def cancel(self, *, scan_id, request, handle, runtime):
        if request.coverage_policy == "all_tracked_files":
            from .coverage_provider import CoverageController

            controller = CoverageController(self, runtime)
            if handle is None:
                try:
                    handle = controller.handle(controller.store.get(scan_id, controller.directory))
                except ValueError:
                    return
            return controller.cancel(handle)
        return self._cancel_raw(scan_id=scan_id, request=request, handle=handle, runtime=runtime)

    def _cancel_raw(self, *, scan_id, request, handle, runtime):
        if handle is None:
            # Read the stable job identity; never submit a new job to cancel an uncertain launch.
            config, session = self._config(runtime, request.session_id)
            if execution_backend_for_session(session, fallback_config=config) == "libvirt_vm":
                self._command(runtime, request.session_id, ["cancel", scan_id])
                return
            backend = container_backend(runtime, config)
            worker_config = backend.config_for_session(request.session_id)
            client = ContainerWorkerClient(
                base_url=worker_config.container_worker_base_url,
                token_env=worker_config.container_worker_token_env,
                timeout_seconds=worker_config.container_worker_request_timeout_seconds,
            )
            job_id = "scan-" + scan_id
            try:
                record = client.get(job_id)
            except ContainerWorkerClientError as exc:
                if exc.status_code == 404:
                    return False
                raise
            if record.session_id != request.session_id:
                raise ValueError("Security scan job belongs to another session")
            if request.coverage_policy == "all_tracked_files" and record.status == "starting":
                raise SecurityScanRetryableError("Worker launch is still being prepared")
            if (
                request.coverage_policy == "all_tracked_files"
                and record.status == "removed"
                and not record.container_id
            ):
                return False
            if record.status in {"starting", "running"}:
                client.stop(job_id)
            client.delete(job_id)
            return True
        data = handle.metadata
        if data["backend"] == "container_worker_vm":
            config, _ = self._config(runtime, data["session_id"])
            container_backend(runtime, config).stop(handle.execution_id)
        else:
            self._command(runtime, data["session_id"], ["cancel", data["scan_id"]])

    def _preserve_result(self, result, handle, runtime):
        data = handle.metadata
        key = "security-scan-" + data["scan_id"]
        store = PublishedArtifactStore(runtime.config_path("runtime_dir"))
        if store.exists(key):
            if store.get(key).metadata.session_id != data["session_id"]:
                raise ValueError("Security scan artifact belongs to another session")
        else:
            files = self._command(runtime, data["session_id"], ["files", data["scan_id"]])["paths"]
            if not files or any(
                not value.startswith("security-scans/" + data["scan_id"] + "/") for value in files
            ):
                raise ValueError("Invalid security scan artifact paths")
            prefix = "security-scans/" + data["scan_id"] + "/"
            # Older images returned raw logs and a whole reports directory.
            # Expand that directory to known canonical files instead of publishing
            # arbitrary future diagnostics by recursively sweeping scan storage.
            canonical = ("report.md", "scan-manifest.json", "findings.json", "coverage.json")
            expanded = []
            for value in files:
                relative = value.removeprefix(prefix)
                if relative == "reports":
                    # Legacy receipts list directories; the result already names
                    # its validated canonical files. Never include raw scan logs.
                    expanded.extend(
                        path
                        for name, path in result.artifacts.items()
                        if name in canonical and path.startswith(prefix + "reports/")
                    )
                else:
                    expanded.append(value)
            allowed = []
            for value in expanded:
                relative = value.removeprefix(prefix)
                if not value.startswith(prefix) or any(
                    part in {"..", ".", ""} for part in relative.split("/")
                ):
                    raise ValueError("Invalid security scan artifact paths")
                if public_report_path(relative):
                    allowed.append(value)
            files = sorted(set(allowed))
            if not files:
                raise ValueError("No public security scan report files")
            publisher = ArtifactPublishArtifactsEffectProvider()
            publisher.initialize(
                ArtifactPublishArtifactsEffectDefinition(
                    id="security-scan-artifacts",
                    display_name="Preserve security scan reports",
                    type="effect:artifact:publish-artifacts",
                    session_id=data["session_id"],
                    key=key,
                    source_paths=files,
                ),
                runtime,
            )
            publisher.execute()
        return result.model_copy(update={"artifacts": {**result.artifacts, "archive_key": key}})
