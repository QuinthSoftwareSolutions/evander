"""Durable host scheduler over the existing bounded session/worker interfaces."""

from contextlib import ExitStack
from datetime import datetime, timedelta
import hashlib
import gzip
import io
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile

import httpx

from evander.execution.container_worker_client import ContainerWorkerClientError
from evander.execution import ExecutionStatusKind
from evander.codex_auth import CodexAuthMode
from evander.effects.providers.agent_session import (
    container_backend,
    execution_backend_for_session,
    effective_codex_auth_mode,
    effective_subscription_profile_id,
    default_agent_model_for_provider,
    container_worker_image_for_session,
)
from evander.published_artifacts import PublishedArtifactStore
from evander.security_scans import (
    SecurityScanHandle,
    SecurityScanRequest,
    SecurityScanResult,
    SecurityScanRetryableError,
)

from .coverage import (
    CoverageStore,
    RESERVE_SECONDS,
    accept,
    aggregate,
    apply_exclusions,
    batch_text_limit,
    begin,
    complete,
    make_state,
    now,
    pending,
    plan_boundaries,
    progress,
    record_open_questions,
    required_work_complete,
    review_parallelism,
    resumable,
    retry,
    valid_path,
)
from .report_files import public_report_path
from .coverage_projection import symlink_projection

LOG = logging.getLogger(__name__)


class CoverageController:
    def __init__(self, provider, runtime):
        self.provider, self.runtime = provider, runtime
        self.store = CoverageStore(runtime.config_path("runtime_dir"))
        self.artifacts = PublishedArtifactStore(runtime.config_path("runtime_dir"))

    @property
    def directory(self):
        return str(self.runtime.metadata.get("directory_path") or "root")

    @property
    def git(self):
        service = getattr(self.runtime.admin_service, "managed_git_admin", None)
        if service is None:
            raise ValueError("Tracked coverage requires managed Git")
        return service

    def route(self, request):
        config, session = self.provider._config(self.runtime, request.session_id)
        if execution_backend_for_session(session, fallback_config=config) != "container_worker_vm":
            raise ValueError("Tracked coverage requires the container worker backend")
        mode = session.metadata.get("model_provider_mode") or config.model_provider_mode
        reasoning = getattr(session, "reasoning_effort", None)
        auth_mode = effective_codex_auth_mode(session=session, config=config)
        if auth_mode == CodexAuthMode.CHATGPT_SUBSCRIPTION and mode != "openai_public":
            raise ValueError("Tracked subscription scans require the public OpenAI route")
        return {
            "model": getattr(session, "model", None)
            or default_agent_model_for_provider("codex", config),
            "reasoning_effort": getattr(reasoning, "value", reasoning) or "high",
            "model_provider_mode": mode,
            "auth_mode": auth_mode.value,
            "subscription_profile_id": effective_subscription_profile_id(session=session),
            "cyber_access_program": getattr(session, "cyber_access_program", None),
            "image": container_worker_image_for_session(session, config),
            "image_alias": (session.metadata.get("container_worker") or {}).get("image_alias")
            if isinstance(session.metadata.get("container_worker"), dict)
            else None,
        }

    def start(self, scan_id, request):
        if request.resume_from_scan_id == scan_id:
            raise ValueError("A resume must use a new workflow execution identity")
        with ExitStack() as locks:
            for identity in sorted(
                {scan_id, *([request.resume_from_scan_id] if request.resume_from_scan_id else [])}
            ):
                locks.enter_context(self.store.locked(identity))
            try:
                state = self.store.get(scan_id, self.directory)
            except ValueError:
                state = None
            if state is not None:
                if SecurityScanRequest.model_validate(state["request"]) != request:
                    raise ValueError("Coverage identity reused with different inputs")
                return self.handle(state)
            route = self.route(request)
            self.git.get_repository(request.managed_repo_id, self.directory)
            inventory = self.provider._command(
                self.runtime,
                request.session_id,
                ["coverage-inventory", json.dumps(request.model_dump(mode="json"))],
            )
            if inventory.get("coverage_runtime") != 1:
                raise ValueError("Scanner image lacks tracked-coverage support; rebuild it")
            previous = (
                self.store.get(request.resume_from_scan_id, self.directory)
                if request.resume_from_scan_id
                else None
            )
            service = getattr(self.runtime, "security_scan_service", None)
            created = service.store.get(scan_id, self.directory).created_at if service else now()
            state = make_state(
                scan_id,
                request,
                self.directory,
                inventory,
                route,
                previous=previous,
                previous_until=self.store.retained_until(previous) if previous else None,
                at=created,
            )
            if previous:
                native_scopes = self.verify_proofs(previous)
                if state.get("exclude_paths"):
                    apply_exclusions(state, native_scopes)
                if "open_questions" not in previous:
                    self.restore_open_questions(state)
            pin_ref = "refs/evander/security-scans/" + scan_id
            self.git.pin_retained_snapshot(
                request.managed_repo_id,
                self.directory,
                binding_kind="security_scan",
                binding_id=scan_id,
                pin_ref=pin_ref,
                commit_sha=state["revision"],
            )
            try:
                self.verify_inventory(state)
                self.store.create(state)
            except Exception:
                self.git.release_retained_snapshot(
                    request.managed_repo_id,
                    self.directory,
                    binding_kind="security_scan",
                    binding_id=scan_id,
                    pin_ref=pin_ref,
                    expected_commit_sha=state["revision"],
                )
                raise
            return self.handle(state)

    def verify_inventory(self, state):
        """Bind the trusted checkout inventory to the authorized host Git tree."""
        bare, _ = self.git.resolve_internal_repository(state["managed_repo_id"], self.directory)
        environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        environment.update(
            GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_NO_REPLACE_OBJECTS="1"
        )
        tree = subprocess.run(
            [
                "git",
                "--git-dir",
                str(bare),
                "ls-tree",
                "-r",
                "-z",
                "-l",
                "--full-tree",
                state["revision"],
            ],
            capture_output=True,
            check=True,
            timeout=120,
            env=environment,
        )
        expected = []
        for record in tree.stdout.split(b"\0"):
            if not record:
                continue
            metadata, path = record.split(b"\t", 1)
            mode, _, oid, size = metadata.split()
            expected.append(
                {
                    "path": path.decode("utf-8", "surrogateescape"),
                    "mode": mode.decode(),
                    "oid": oid.decode(),
                    "size": 0 if size == b"-" else int(size),
                }
            )
        supplied = [
            {k: item[k] for k in ("path", "mode", "oid", "size")} for item in state["inventory"]
        ]
        if sorted(expected, key=lambda x: x["path"]) != sorted(supplied, key=lambda x: x["path"]):
            raise ValueError(
                "Session checkout does not match the selected managed repository snapshot"
            )

    @staticmethod
    def handle(state):
        return SecurityScanHandle(
            execution_id=state["scan_id"],
            metadata={
                "coverage_policy": "all_tracked_files",
                "scan_id": state["scan_id"],
                "directory_path": state["directory_path"],
            },
        )

    def archive_members(self, key, expected_sha=None):
        artifact = self.artifacts.get(key)
        hasher = hashlib.sha256()
        with artifact.blob_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        if hasher.hexdigest() != artifact.metadata.sha256 or (
            expected_sha and expected_sha != artifact.metadata.sha256
        ):
            raise ValueError("Retained review archive failed integrity verification")
        members = {}
        total = 0
        with tarfile.open(artifact.blob_path, "r:gz") as source:
            for member in source:
                path = PurePosixPath(member.name)
                if member.isdir():
                    continue
                if (
                    not member.isfile()
                    or path.is_absolute()
                    or ".." in path.parts
                    or member.size > 32 * 1024 * 1024
                ):
                    raise ValueError("Unsafe retained review artifact")
                if member.name in members:
                    raise ValueError("Duplicate retained artifact path")
                # Reapply exactly the publication allowlist, including legacy bundles.
                if not (
                    len(path.parts) > 2
                    and path.parts[0] == "security-scans"
                    and public_report_path("/".join(path.parts[2:]))
                ):
                    continue
                total += member.size
                if total > 128 * 1024 * 1024:
                    raise ValueError("Review archive exceeds the retained report limit")
                members[member.name] = source.extractfile(member).read()
        return artifact, members

    def verify_proofs(self, state):
        scopes = {}
        accepted = {proof["archive_key"] for proof in state["proofs"]}
        for proof in [*state["proofs"], *state["uncredited"]]:
            _, members = self.archive_members(proof["archive_key"], proof["archive_sha256"])
            if proof["archive_key"] not in accepted:
                continue
            manifest = members.get(proof.get("report_prefix", "") + "scan-manifest.json")
            if manifest is not None:
                scan = json.loads(manifest).get("scan", {})
                if scan.get("id") != proof["provider_scan_id"]:
                    raise ValueError("Retained native review identity differs from its proof")
                paths = scan.get("scope", {}).get("includePaths", [])
                if isinstance(paths, list) and all(isinstance(path, str) for path in paths):
                    scopes[proof["provider_scan_id"]] = paths
        return scopes

    def restore_open_questions(self, state):
        """Reclassify legacy question blockers only from preserved accepted reports.

        This changes no file credit and never edits the source run of a resume.
        A genuine deferred item, even one with a colliding ID, remains required.
        """
        questions, required = {}, set()
        for proof in state["proofs"]:
            _, members = self.archive_members(proof["archive_key"], proof["archive_sha256"])
            prefix = proof["report_prefix"]
            scan = json.loads(members[prefix + "scan-manifest.json"])["scan"]
            body = members[prefix + "coverage.json"]
            seals = [x for x in scan["artifacts"] if x.get("path") == "coverage.json"]
            if (
                scan["id"] != proof["provider_scan_id"]
                or scan["target"].get("kind") != "git_revision"
                or scan["target"].get("revision") != state["revision"]
                or scan.get("status") != "completed"
                or not scan.get("sealedAt")
                or len(seals) != 1
                or seals[0].get("sha256") != hashlib.sha256(body).hexdigest()
            ):
                raise ValueError("Retained question provenance failed verification")
            coverage = json.loads(body)
            required.update(
                proof["provider_scan_id"] + ":" + str(item["id"])
                for item in coverage.get("deferred", [])
            )
            record_open_questions(
                {"open_questions": questions}, coverage.get("openQuestions", []), proof
            )
        removed = set()
        for key, question in questions.items():
            if key not in required and state["deferred"].get(key) == {
                "reason": question["question"],
                "resolution": None,
            }:
                del state["deferred"][key]
                removed.add(key)
        for item in state["tasks"].values():
            item["deferred_keys"] = [k for k in item["deferred_keys"] if k not in removed]
        state["open_questions"] = questions

    def retain_attempt(self, state, attempt, child, result):
        known = {
            key: value for key, value in result.items() if key in SecurityScanResult.model_fields
        }
        preserved = self.provider._preserve_result(
            SecurityScanResult.model_validate(known), child, self.runtime
        )
        artifact, _ = self.archive_members(preserved.artifacts["archive_key"])
        if not any(
            source["archive_key"] == artifact.metadata.key
            for source in [*state["proofs"], *state["uncredited"]]
        ):
            state["uncredited"].append(
                {
                    "archive_key": artifact.metadata.key,
                    "archive_sha256": artifact.metadata.sha256,
                    "review_id": attempt["scan_id"],
                }
            )
        attempt["retention_pending"] = False
        self.store.save(state)

    def proof(self, result, handle, receipt, *, state=None, item=None):
        projection = None
        if state is not None:
            if (
                receipt.get("revision") != state["revision"]
                or receipt.get("kind") != item["kind"]
                or sorted(receipt.get("paths", [])) != item["paths"]
            ):
                raise ValueError("Review result does not match the pinned scope")
            if item["kind"] != "architecture" and any(
                x["mode"] == "120000" and x["path"] in item["paths"] for x in state["inventory"]
            ):
                bare, _ = self.git.resolve_internal_repository(
                    state["managed_repo_id"], self.directory
                )
                try:
                    projection = symlink_projection(bare, state["revision"], item["paths"])
                except (OSError, subprocess.SubprocessError, RecursionError) as exc:
                    raise ValueError("Source symlink projection could not be verified") from exc
        if receipt.get("source_projection") != projection:
            raise ValueError("Review symlink projection does not match the source snapshot")
        known = {
            key: value for key, value in result.items() if key in SecurityScanResult.model_fields
        }
        native = SecurityScanResult.model_validate(known)
        preserved = self.provider._preserve_result(native, handle, self.runtime)
        key = preserved.artifacts["archive_key"]
        artifact, members = self.archive_members(key)
        reports = [name for name in members if name.endswith("/scan-manifest.json")]
        if len(reports) != 1:
            raise ValueError("Expected one native review manifest")
        prefix = reports[0].rsplit("/", 1)[0] + "/"
        manifest = json.loads(members[prefix + "scan-manifest.json"])
        findings = json.loads(members[prefix + "findings.json"])
        coverage = json.loads(members[prefix + "coverage.json"])
        scan = manifest["scan"]
        expected = ["."] if receipt["kind"] == "architecture" else receipt["paths"]
        expected_revision = projection["revision"] if projection else receipt["revision"]
        if (
            scan["target"].get("kind") != "git_revision"
            or scan["target"].get("revision") != expected_revision
            or sorted(scan["scope"]["includePaths"]) != sorted(expected)
            or not scan.get("sealedAt")
            or scan.get("status") != "completed"
        ):
            raise ValueError("Retained native scope or seal does not match its review")
        # Recheck every retained sealed artifact after transport, not only the
        # two canonical JSON documents. Private evidence remains unpublished.
        records, collisions = {}, set()
        for record in scan.get("artifacts", []):
            if not isinstance(record, dict):
                raise ValueError("Invalid native artifact seal")
            path = valid_path(record.get("path"))
            collision = path.lower()
            sha256 = record.get("sha256")
            if (
                collision in collisions
                or not isinstance(sha256, str)
                or not re.fullmatch(r"[a-f0-9]{64}", sha256)
            ):
                raise ValueError("Invalid native artifact seal")
            collisions.add(collision)
            records[path] = record
            body = members.get(prefix + path)
            if body is None:
                if public_report_path("reports/review/" + path):
                    raise ValueError("Sealed native report is missing")
                continue
            if record["sha256"] != hashlib.sha256(body).hexdigest():
                raise ValueError("Native artifact seal verification failed")
        if not {"findings.json", "coverage.json"}.issubset(records):
            raise ValueError("Native canonical seal verification failed")
        if receipt["coverage"] != coverage["completeness"] or receipt["deferred"] != coverage.get(
            "deferred", []
        ):
            raise ValueError("Native coverage receipt was changed")
        if scan.get("scope", {}).get("excludePaths") or coverage.get("excludePaths"):
            raise ValueError("An exact-scope review cannot omit assigned paths")
        # Derive credit-bearing fields from the sealed native coverage, never
        # from a model-writable wrapper file or aggregate progress count.
        dispositions, resolutions = {}, {}
        for surface in coverage.get("surfaces", []):
            label, disposition, reason = (
                surface.get("label", ""),
                surface.get("disposition"),
                surface.get("notes"),
            )
            if (
                label.startswith("file:")
                and reason
                and disposition in {"reported", "no_issue_found", "not_applicable"}
            ):
                dispositions[label[5:]] = {
                    "disposition": "not_applicable"
                    if disposition == "not_applicable"
                    else "reviewed",
                    "reason": reason,
                }
            if label.startswith("deferred:") and reason:
                resolutions[label[9:]] = {"disposition": disposition, "reason": reason}
        model = scan.get("threatModel")
        receipt.update(
            exclusions=coverage.get("explicitExclusions", []),
            needs_follow_up=any(
                s.get("disposition") == "needs_follow_up" for s in coverage.get("surfaces", [])
            ),
            open_questions=coverage.get("openQuestions", []),
            dispositions=dispositions,
            resolutions=resolutions,
            architecture=({"threat_model": model, "paths": []} if model else None),
        )
        if model:
            model_text = json.dumps(model)
            receipt["architecture"]["paths"] = [p for p in receipt["paths"] if p in model_text]
        return {
            "archive_key": key,
            "archive_sha256": artifact.metadata.sha256,
            "provider_scan_id": scan["id"],
            "target_id": scan["target"].get("targetId"),
            "findings": findings["findings"],
            "report_prefix": prefix,
            **({"source_projection": projection} if projection else {}),
        }

    def launch(self, state, item):
        attempt = item["attempts"][-1]
        request = SecurityScanRequest.model_validate(state["request"])
        # A lost response retries this exact durable generation, never a fresh job.
        remaining = (
            datetime.fromisoformat(state["deadline_at"]) - now()
        ).total_seconds() - RESERVE_SECONDS
        timeout = max(1, min(3600, remaining))
        architecture = state["architecture"]
        if state.get("exclude_paths"):
            architecture = {
                **(architecture or {}),
                "host_review_exclusions": {
                    "paths": state["exclude_paths"],
                    "policy": "These paths are outside the assigned review. The host reports them as unreviewed exclusions and retains earlier findings. Do not make their content review a new obligation for this assigned scope.",
                },
            }
        payload = attempt.get("payload") or {
            **request.model_dump(mode="json"),
            "scan_id": attempt["scan_id"],
            "timeout_seconds": timeout,
            "snapshot_commit": state["revision"],
            "frozen_route": state["route"],
            "sdk_version": state["sdk_version"],
            "max_parallel_reviews": review_parallelism(state),
            "coverage_task": {
                "kind": item["kind"],
                "paths": item["paths"],
                "assigned_entries": [x for x in state["inventory"] if x["path"] in item["paths"]],
                "inventory_paths": list(state["files"]),
                "architecture": architecture,
                "deferred": {
                    k: v
                    for k, v in state["deferred"].items()
                    if not v.get("resolution")
                    and item["kind"] == "boundary" and k in item["deferred_keys"]
                },
                "accepted_file_reviews": {
                    p: x["receipt"]
                    for p, x in state["files"].items()
                    if x["disposition"] in {"reviewed", "not_applicable"}
                },
                "prior_findings": aggregate(state)[2],
            },
        }
        attempt["payload"] = payload
        self.store.save(state)
        # Requests can contain thousands of path names; never put them in argv.
        from evander.effects.providers.agent_copy_files import AgentCopyFilesEffectProvider
        from evander.events.provider_models import AgentCopyFilesEffectDefinition

        staging = self.store.root / "requests"
        staging.mkdir(mode=0o700, exist_ok=True)
        source = staging / (attempt["scan_id"] + ".json")
        body = json.dumps(payload, ensure_ascii=True).encode()
        if source.exists():
            if source.read_bytes() != body:
                raise ValueError("Frozen review request changed")
        else:
            with source.open("xb") as stream:
                source.chmod(0o600)
                stream.write(body)
        destination = "/home/agent/.evander/security-scan-inputs/" + source.name
        copier = AgentCopyFilesEffectProvider()
        copier.initialize(
            AgentCopyFilesEffectDefinition(
                id="coverage-input",
                display_name="Stage frozen review input",
                type="effect:agent:copy-files",
                session_id=request.session_id,
                allowed_source_roots=[str(staging)],
                files=[
                    {
                        "source_path": str(source),
                        "destination_path": destination,
                        "mode": "0600",
                        "overwrite": True,
                    }
                ],
            ),
            self.runtime,
        )
        if not attempt.get("input_staged"):
            try:
                copier.execute()
            except ContainerWorkerClientError as exc:
                if isinstance(exc.__cause__, httpx.TransportError) or exc.status_code in {
                    500,
                    502,
                    503,
                    504,
                }:
                    raise SecurityScanRetryableError(
                        "Review input staging was interrupted"
                    ) from exc
                raise
            except (
                httpx.TransportError,
                TimeoutError,
                ConnectionError,
                subprocess.TimeoutExpired,
            ) as exc:
                raise SecurityScanRetryableError("Review input staging was interrupted") from exc
            attempt["input_staged"] = True
            self.store.save(state)
        attempt["launch_submitted"] = True
        self.store.save(state)
        child = self.provider._start_raw(
            scan_id=attempt["scan_id"],
            request=request,
            runtime=self.runtime,
            payload={"_request_file": destination},
            frozen_route=state["route"],
        )
        attempt["handle"] = child.model_dump(mode="json")
        self.store.save(state)

    def try_launch(self, state, item):
        attempt = item["attempts"][-1]
        if attempt.get("launch_retry_at") and now() < datetime.fromisoformat(
            attempt["launch_retry_at"]
        ):
            return
        try:
            self.launch(state, item)
        except SecurityScanRetryableError:
            failures = attempt.get("launch_failures", 0) + 1
            attempt["launch_failures"] = failures
            attempt["launch_retry_at"] = (
                now() + timedelta(seconds=5 if failures == 1 else 30)
            ).isoformat()
            if failures >= 3:
                state["stop_reason"] = "Worker launch could not be confirmed after bounded retries"
                item["status"] = "blocked"
            self.store.save(state)

    def backend(self, state):
        config, _ = self.provider._config(self.runtime, state["request"]["session_id"])
        return container_backend(self.runtime, config)

    def poll(self, handle):
        scan_id = handle.metadata["scan_id"]
        with self.store.locked(scan_id):
            state = self.store.get(scan_id, self.directory)
            if state["finished_at"]:
                self.cleanup(state)
                if not state["archive_key"] and not state.get("migration_error"):
                    self.publish(state)
                self.store.save(state)
                return self.output(state)
            if "open_questions" not in state:
                try:
                    self.restore_open_questions(state)
                except (ValueError, KeyError, OSError, tarfile.TarError):
                    LOG.warning(
                        "Could not verify stored security coverage for scan %s",
                        scan_id,
                        exc_info=True,
                    )
                    # Keep all existing credit and evidence untouched, but do
                    # not let unverifiable history bypass cancellation cleanup.
                    state["migration_error"] = (
                        "Stored review evidence could not be verified; aggregate report unavailable"
                    )
                    state["stop_reason"] = state["stop_reason"] or state["migration_error"]
                self.store.save(state)
            request = SecurityScanRequest.model_validate(state["request"])
            if not state["stop_reason"]:
                try:
                    if self.route(request) != state["route"]:
                        state["stop_reason"] = "Scanner model or access configuration changed"
                except (ValueError, LookupError):
                    state["stop_reason"] = "Scanner model or access configuration is unavailable"
            if now() >= datetime.fromisoformat(state["deadline_at"]) - timedelta(
                seconds=RESERVE_SECONDS
            ):
                state["stop_reason"] = state["stop_reason"] or "Review time budget reached"
            for item in list(state["tasks"].values()):
                if item["status"] != "running":
                    continue
                attempt = item["attempts"][-1]
                if attempt["handle"] is None:
                    if state["stop_reason"]:
                        child = SecurityScanHandle(
                            execution_id="scan-" + attempt["scan_id"],
                            metadata={
                                "backend": "container_worker_vm",
                                "session_id": request.session_id,
                                "scan_id": attempt["scan_id"],
                            },
                        )
                    else:
                        self.try_launch(state, item)
                        continue
                else:
                    child = SecurityScanHandle.model_validate(attempt["handle"])
                backend = self.backend(state)
                if state["stop_reason"]:
                    try:
                        self.provider._cancel_raw(
                            scan_id=attempt["scan_id"],
                            request=request,
                            handle=child,
                            runtime=self.runtime,
                        )
                    except (ValueError, RuntimeError, OSError):
                        item["cleanup_pending"] = True
                        item["status"] = "blocked"
                        continue
                try:
                    inspected = backend.inspect(child.execution_id)
                except (RuntimeError, OSError):
                    # Preserve the claim and current job identity while transport is down.
                    continue
                try:
                    result = self.provider._command(
                        self.runtime, request.session_id, ["status", attempt["scan_id"]]
                    )
                except (ValueError, RuntimeError):
                    if inspected.status in {
                        ExecutionStatusKind.RUNNING,
                        ExecutionStatusKind.PREPARING,
                        ExecutionStatusKind.STARTING,
                    }:
                        continue
                    result = {"status": "failed"}
                if result.get("status") == "running" and inspected.status not in {
                    ExecutionStatusKind.FAILED,
                    ExecutionStatusKind.STOPPED,
                    ExecutionStatusKind.DESTROYED,
                }:
                    continue
                if not result.get("coverage_unit") and inspected.status in {
                    ExecutionStatusKind.FAILED,
                    ExecutionStatusKind.STOPPED,
                    ExecutionStatusKind.DESTROYED,
                }:
                    try:
                        result = self.provider._command(
                            self.runtime,
                            request.session_id,
                            ["coverage-recover", attempt["scan_id"]],
                        )
                    except (ValueError, RuntimeError, OSError):
                        # Other children still need polling and cancellation.
                        continue
                    if result.get("recovered_from_saved_sdk_result"):
                        attempt["recovered_saved_result"] = True
                if result.get("failure_category") in {"authentication", "policy"}:
                    state["stop_reason"] = "Scanner access or policy prevented review"
                try:
                    self.retain_attempt(state, attempt, child, result)
                except (ValueError, KeyError, RuntimeError, OSError):
                    # Keep the owned job until its immutable reports are durable.
                    # This occupies a concurrency slot and survives a restart.
                    attempt["retention_pending"] = True
                    self.store.save(state)
                    continue
                receipt = result.get("coverage_unit")
                if receipt:
                    try:
                        proof = self.proof(result, child, receipt, state=state, item=item)
                        accept(state, item, attempt["scan_id"], receipt, proof)
                        state["uncredited"] = [
                            source
                            for source in state["uncredited"]
                            if source["archive_key"] != proof["archive_key"]
                        ]
                    except (ValueError, KeyError) as exc:
                        retry(state, item, "Review integrity or scope validation failed")
                        attempt["validation_error"] = type(exc).__name__
                else:
                    category = result.get("failure_category")
                    if category in {"authentication", "policy"}:
                        state["stop_reason"] = "Scanner access or policy prevented review"
                    retry(state, item, "Worker review was interrupted or produced no sealed result")
                    if category == "quota":
                        state["not_before"] = (
                            now()
                            + timedelta(
                                seconds=max(
                                    30, min(21600, result.get("retry_after_seconds") or 900)
                                )
                            )
                        ).isoformat()
                        state["phase"] = "waiting_for_quota"
                        if item["status"] == "blocked":
                            state["stop_reason"] = (
                                "Subscription quota unavailable after bounded retries"
                            )
                # Publication and journal credit precede removal, including restart recovery.
                self.store.save(state)
                try:
                    backend.destroy(child.execution_id)
                    attempt["cleanup_confirmed"] = True
                except (ValueError, RuntimeError, OSError):
                    attempt["cleanup_pending"] = True
                if state["stop_reason"] and item["status"] not in {"completed", "split"}:
                    item["status"] = "blocked"
            self.cleanup(state)
            plan_boundaries(state)
            for item in pending(state):
                begin(state, item)
                self.store.save(state)
                self.try_launch(state, item)
            active = any(x["status"] == "running" for x in state["tasks"].values())
            queued = any(x["status"] == "pending" for x in state["tasks"].values())
            self.cleanup(state)
            mapping_blocked = state["architecture"] is None and not any(
                x["kind"] == "architecture" and x["status"] in {"pending", "running"}
                for x in state["tasks"].values()
            )
            if state["stop_reason"] or (not active and (not queued or mapping_blocked)):
                if not state["stop_reason"]:
                    state["stop_reason"] = (
                        None if required_work_complete(state) else "Review blocked with unfinished coverage"
                    )
                state["finished_at"] = now().isoformat()
                if state["cleanup_confirmed"]:
                    state["cleanup_finished_at"] = state["finished_at"]
                state["phase"] = "finished"
                self.store.save(state)
                if not state.get("migration_error"):
                    self.publish(state)
            self.store.save(state)
            return self.output(state)

    def cleanup(self, state):
        backend = None
        for item in state["tasks"].values():
            for attempt in item["attempts"]:
                if attempt.get("cleanup_confirmed") or (
                    item["status"] == "running"
                    and attempt is item["attempts"][-1]
                    and not state["finished_at"]
                    and not state["stop_reason"]
                ):
                    continue
                try:
                    backend = backend or self.backend(state)
                    execution_id = (attempt.get("handle") or {}).get(
                        "execution_id", "scan-" + attempt["scan_id"]
                    )
                    if not attempt.get("handle") and not attempt.get("launch_submitted"):
                        attempt["cleanup_confirmed"] = True
                        continue
                    try:
                        observed = backend.inspect(execution_id)
                    except ValueError:
                        # A launch may have reached the worker before its local
                        # mapping was saved. Resolve only the stable owned job ID.
                        found = self.provider._cancel_raw(
                            scan_id=attempt["scan_id"],
                            request=SecurityScanRequest.model_validate(state["request"]),
                            handle=None,
                            runtime=self.runtime,
                        )
                        # An unknown launch may still be queued at the worker.
                        # A 404 alone cannot prove that it will never start.
                        attempt["cleanup_confirmed"] = found is True
                        continue
                    if observed.status != ExecutionStatusKind.DESTROYED:
                        if observed.status not in {
                            ExecutionStatusKind.STOPPED,
                            ExecutionStatusKind.FAILED,
                        }:
                            backend.stop(execution_id)
                        if attempt.get("retention_pending"):
                            child = SecurityScanHandle(
                                execution_id=execution_id,
                                metadata={
                                    "backend": "container_worker_vm",
                                    "session_id": state["request"]["session_id"],
                                    "scan_id": attempt["scan_id"],
                                },
                            )
                            self.retain_attempt(
                                state, attempt, child, {"status": "failed", "coverage": "partial"}
                            )
                        backend.destroy(execution_id)
                    attempt["cleanup_confirmed"] = True
                except (ValueError, RuntimeError, OSError):
                    pass
        state["cleanup_confirmed"] = all(
            a.get("cleanup_confirmed") for t in state["tasks"].values() for a in t["attempts"]
        )
        if (
            state["finished_at"]
            and state["cleanup_confirmed"]
            and not state.get("cleanup_finished_at")
        ):
            state["cleanup_finished_at"] = now().isoformat()

    def cancel(self, handle):
        with self.store.locked(handle.metadata["scan_id"]):
            state = self.store.get(handle.metadata["scan_id"], self.directory)
            state["stop_reason"] = "Workflow execution cancelled"
            self.store.save(state)
        return self.poll(handle)

    def prune(self):
        removed = 0
        for expired in self.store.expired():
            if expired["directory_path"] != self.directory:
                continue
            with self.store.locked(expired["scan_id"]):
                candidates = {s["scan_id"]: s for s in self.store.expired()}
                if expired["scan_id"] not in candidates:
                    continue
                self.git.release_retained_snapshot(
                    expired["managed_repo_id"],
                    self.directory,
                    binding_kind="security_scan",
                    binding_id=expired["scan_id"],
                    pin_ref="refs/evander/security-scans/" + expired["scan_id"],
                    expected_commit_sha=expired["revision"],
                )
                with self.store.connect() as connection:
                    connection.execute(
                        "DELETE FROM campaigns WHERE scan_id=? AND active=0", (expired["scan_id"],)
                    )
                for item in expired["tasks"].values():
                    for attempt in item["attempts"]:
                        (self.store.root / "requests" / (attempt["scan_id"] + ".json")).unlink(
                            missing_ok=True
                        )
                removed += 1
        return removed

    def output(self, state):
        findings, counts, _ = aggregate(state)
        finished = bool(state["finished_at"])
        error = state["stop_reason"]
        if finished and state.get("exclude_paths") and required_work_complete(state) and not error:
            error = "Review completed with explicit unreviewed exclusions"
        if state.get("migration_error") and state["migration_error"] != error:
            error = f"{error}; {state['migration_error']}" if error else state["migration_error"]
        return SecurityScanResult(
            status=("completed" if required_work_complete(state) else "failed") if finished else "running",
            coverage="complete" if complete(state) else "partial",
            to_commit=state["revision"],
            findings=findings,
            severity_counts=counts,
            coverage_progress=progress(state),
            cleanup_confirmed=state["cleanup_confirmed"],
            error=error,
            artifacts=(
                {
                    "archive_key": state["archive_key"],
                    **{
                        name: "security-scans/" + state["scan_id"] + "/reports/aggregate/" + name
                        for name in (
                            "report.md",
                            "scan-manifest.json",
                            "findings.json",
                            "coverage.json",
                        )
                    },
                }
                if state["archive_key"]
                else {}
            ),
        )

    def publish(self, state):
        key = "security-scan-" + state["scan_id"]
        if self.artifacts.exists(key):
            metadata = self.artifacts.get(key).metadata
            if metadata.metadata.get("coverage_scan_id") != state["scan_id"]:
                raise ValueError("Aggregate artifact identity collision")
            state["archive_key"] = key
            return
        findings, counts, full = aggregate(state)
        prefix = "security-scans/" + state["scan_id"] + "/reports/aggregate/"
        data = {
            "coverage.json": {
                "documentType": "evander.security-coverage",
                "version": 1,
                "batching_version": state.get("batching_version", 1),
                "max_batch_text_bytes": batch_text_limit(state),
                "max_parallel_reviews": review_parallelism(state),
                "exclude_paths": state.get("exclude_paths", []),
                "incomplete_review_policy": state.get("incomplete_review_policy", "split"),
                "pending_batch_rebuild": state.get("pending_batch_rebuild"),
                "revision": state["revision"],
                "inventory_digest": state["inventory_digest"],
                "inventory": state["inventory"],
                "completeness": "complete" if complete(state) else "partial",
                "progress": progress(state),
                "files": state["files"],
                "deferred": state["deferred"],
                "open_questions": state.get("open_questions", {}),
                "remaining_scopes": [
                    {"kind": x["kind"], "paths": x["paths"], "status": x["status"]}
                    for x in state["tasks"].values()
                    if x["status"] not in {"completed", "split"}
                ],
            },
            "findings.json": {
                "documentType": "evander.security-findings",
                "version": 1,
                "findings": full,
                "severity_counts": counts,
            },
            "scan-manifest.json": {
                "documentType": "evander.security-scan",
                "version": 1,
                "scan_id": state["scan_id"],
                "managed_repo_id": state["managed_repo_id"],
                "revision": state["revision"],
                "lineage": state["lineage"],
                "coverage_policy": "all_tracked_files",
                "exclude_paths": state.get("exclude_paths", []),
                "incomplete_review_policy": state.get("incomplete_review_policy", "split"),
                "pending_batch_rebuild": state.get("pending_batch_rebuild"),
                "batching_version": state.get("batching_version", 1),
                "max_batch_text_bytes": batch_text_limit(state),
                "max_parallel_reviews": review_parallelism(state),
                "completed_at": state["finished_at"],
                "stop_reason": state["stop_reason"],
                "uncredited_sources": state["uncredited"],
                "sources": [
                    {
                        **{
                            k: p[k]
                            for k in ("archive_key", "archive_sha256", "provider_scan_id", "report_prefix")
                        },
                        **(
                            {"source_projection": p["source_projection"]}
                            if p.get("source_projection") else {}
                        ),
                    }
                    for p in state["proofs"]
                ],
            },
        }
        if state.get("subscription_handoff"):
            for name in ("coverage.json", "scan-manifest.json"):
                data[name]["subscription_handoff"] = state["subscription_handoff"]
        p = progress(state)
        report = [
            "# Repository security review",
            "",
            "Scan: " + state["scan_id"],
            "Repository: " + state["managed_repo_id"],
            "Revision: " + state["revision"],
            "Coverage: " + ("complete" if complete(state) else "partial"),
            f"Files: {p['files_reviewed']} reviewed, {p['files_not_applicable']} not applicable, {p['files_blocked']} blocked, {p['files_pending']} pending, {p['files_excluded']} explicitly excluded; {p['files_total']} total.",
            "",
            "## Explicit file exclusions",
            "",
            *[f"- {path}: excluded by request; any earlier accepted findings and review credit remain retained." for path in state.get("exclude_paths", [])],
            "",
            "## Findings",
            "",
            *[f"- {x['severity']}: {x['title']}" for x in findings],
            "",
            "## Open questions and limitations",
            "",
            *[
                f"- {x['question']} (Source: {x['receipt']})"
                + (f" Follow-up: {x['followUpPrompt']}" if x.get("followUpPrompt") else "")
                for x in state.get("open_questions", {}).values()
            ],
            "",
            "## Remaining work",
            "",
            state["stop_reason"]
            or (
                "The non-excluded review is complete; explicitly excluded paths remain outside this assessment. Live deployment behavior was not tested."
                if state.get("exclude_paths")
                else "The requested static review is complete. Live deployment behavior was not tested."
            ),
            "",
            "Resume with a real bot mention: resume security scan " + state["scan_id"],
            "",
            "Native reports and evidence are retained unchanged in sources/. See coverage.json for every file disposition.",
            "This is a static review. Live deployment behavior was not tested.",
        ]
        if any(p.get("source_projection") for p in state["proofs"]):
            report.extend([
                "",
                "Symlink metadata reviews use derived Git snapshots. scan-manifest.json records each verified projection back to the original revision and link objects; destination files receive no automatic coverage credit.",
            ])
        manifest = data.pop("scan-manifest.json")
        content = {
            prefix + name: json.dumps(value, sort_keys=True, ensure_ascii=True).encode()
            for name, value in data.items()
        }
        content[prefix + "report.md"] = "\n".join(report).encode("utf-8", "backslashreplace")
        index, seen = [], set()
        # A large repository can produce many native reports. Keep only one
        # bounded unit archive in memory and stream the aggregate to host disk.
        with tempfile.NamedTemporaryFile(
            prefix=".aggregate-", suffix=".tar.gz", dir=self.store.root
        ) as temporary:
            with gzip.GzipFile(filename="", fileobj=temporary, mode="wb", mtime=0) as compressed:
                with tarfile.open(
                    fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT
                ) as archive:

                    def add(name, body, *, credit=True):
                        if name in seen:
                            raise ValueError("Duplicate aggregate report member")
                        seen.add(name)
                        entry = tarfile.TarInfo(name)
                        entry.size, entry.mode, entry.mtime = len(body), 0o600, 0
                        archive.addfile(entry, io.BytesIO(body))
                        if credit:
                            index.append(
                                {
                                    "path": name.removeprefix(prefix),
                                    "sha256": hashlib.sha256(body).hexdigest(),
                                    "size": len(body),
                                }
                            )

                    for name, body in sorted(content.items()):
                        add(name, body)
                    for proof in state["proofs"]:
                        _, files = self.archive_members(
                            proof["archive_key"], proof["archive_sha256"]
                        )
                        for name, body in sorted(files.items()):
                            add(prefix + "sources/" + proof["archive_key"] + "/" + name, body)
                    for source in state["uncredited"]:
                        _, files = self.archive_members(
                            source["archive_key"], source["archive_sha256"]
                        )
                        for name, body in sorted(files.items()):
                            add(prefix + "uncredited/" + source["review_id"] + "/" + name, body)
                    manifest["artifacts"] = index
                    body = json.dumps(manifest, sort_keys=True, ensure_ascii=True).encode()
                    add(prefix + "scan-manifest.json", body, credit=False)
            temporary.flush()
            self.artifacts.publish_archive(
                key=key,
                session_id=state["request"]["session_id"],
                archive_path=Path(temporary.name),
                metadata={"coverage_scan_id": state["scan_id"]},
                source_file_count=len(seen),
                source_byte_count=sum(x["size"] for x in index) + len(body),
            )
        state["archive_key"] = key


def describe(record, runtime_dir):
    if not (Path(runtime_dir) / "codex-security-coverage/journal.sqlite3").is_file():
        return {"resumable": False, "resumable_until": None, "pinned_ref": None}
    try:
        store = CoverageStore(runtime_dir)
        state = store.get(record.scan_id, record.directory_path)
    except ValueError:
        return {"resumable": False, "resumable_until": None, "pinned_ref": None}
    until = store.retained_until(state)
    return {
        "resumable": resumable(state, until=until),
        "resumable_until": until.isoformat() if state["finished_at"] else None,
        "lineage": state["lineage"],
        "inventory_digest": state["inventory_digest"],
        "execution_configuration": {
            key: state["route"].get(key)
            for key in (
                "model",
                "reasoning_effort",
                "auth_mode",
                "subscription_profile_id",
                "cyber_access_program",
                "model_provider_mode",
                "image",
                "image_alias",
            )
        },
        "pinned_ref": "refs/evander/security-scans/" + state["scan_id"],
        "to_commit": state["revision"],
        "coverage_progress": progress(state),
        "cleanup_confirmed": state["cleanup_confirmed"],
    }
