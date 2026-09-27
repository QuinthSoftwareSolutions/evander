from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from pydantic import ValidationError

from evander.privacy.datasets import DatasetRegistry, default_registry
from evander.privacy.models import (
    CaseCreate,
    FollowupCreate,
    HoldCreate,
    IncidentCreate,
    PrivacyConflict,
    PrivacyError,
    PrivacySettings,
    PrivacyUnsupported,
    RecordReference,
    merge_privacy_settings,
    now_utc,
    revision,
)
from evander.privacy.repository import PrivacyRepository, privacy_guard

MAX_SELECTION = 1000
MAX_EXPORT_BYTES = 32 * 1024 * 1024
MAX_FAILED_ATTEMPTS = 3


class PrivacyAdminService:
    """Revisioned operator workflow, never a generic SQL/filesystem delete tool.

    Domain adapters own source semantics. Unsupported operations fail explicitly;
    metadata-only case management does not imply complete rights fulfilment.
    """

    def __init__(self, operations, resolver, config, *, registry: DatasetRegistry | None = None):
        self.operations = operations
        self.resolver = resolver
        self.config = config
        self.runtime_dir = Path(config.runtime_dir)
        self.repository = PrivacyRepository(self.runtime_dir)
        self.registry = registry or default_registry(config)

    def _scope(self, scope: str) -> str:
        # The supported resolver validates existence; never normalize an unknown
        # child or missing record owner into the installation-wide root scope.
        return self.resolver.directory_scope_resolver.resolve_directory_path(scope)

    def _settings(self, scope: str) -> PrivacySettings:
        return self.resolver.resolve_for_directory(scope).privacy

    def _config_view(self, scope):
        result = self.operations.read_directory_domain_config(scope, "privacy")
        if not result.ok or result.data is None:
            raise PrivacyError("Privacy configuration could not be read")
        effective = self._settings(scope).model_dump(mode="json")
        root = self._settings("root").model_dump(mode="json")
        overlay = dict(result.data.config or {}).get("privacy", {})
        return {"config": overlay, "effective_config": effective, "root_config": root}

    def _policy_revision(self, scope):
        return revision(self._config_view(scope))

    def _revision(self, scope):
        return revision(
            {
                "scope": scope,
                "policy": self._config_view(scope),
                "generation": self.repository.generation(),
            }
        )

    @staticmethod
    def _summary(record):
        allowed = {
            "id",
            "status",
            "scope",
            "created_at",
            "updated_at",
            "dataset",
            "operation",
            "case_id",
            "record_count",
            "review_at",
            "deadline_at",
            "owner",
            "kind",
            "request_type",
            "expires_at",
            "review_reference",
            "reviewed_by",
        }
        return {key: value for key, value in record.items() if key in allowed}

    def snapshot(self, scope: str) -> dict[str, Any]:
        scope = self._scope(scope)
        with privacy_guard(self.runtime_dir):
            view = self._config_view(scope)
            inventory = self.registry.inventory(scope)
            gaps = [
                row["dataset_id"]
                for row in inventory
                if not all(row["capabilities"].get(key) for key in ("locate", "erase", "hold"))
            ]
            blockers = [f"Manual lifecycle coverage: {key}" for key in gaps]
            blockers += [
                "Provider coverage is limited to model relay, classifier and subscription "
                "egress; other outbound paths require deployment controls.",
                "Legal periods, external copies, backups and deployment evidence require review.",
            ]
            result = {
                "directory_path": scope,
                "revision": self._revision(scope),
                **view,
                "inventory": inventory,
                "coverage_complete": False,
                "readiness": {"ready": False, "blockers": blockers},
                "limits": {"scan_records_per_source": 10000, "selection_records": MAX_SELECTION},
            }
            for kind, key in (
                ("hold", "holds"),
                ("case", "cases"),
                ("job", "jobs"),
                ("followup", "followups"),
                ("incident", "incidents"),
            ):
                records = self.repository.list(kind, scope, limit=101)
                result[key] = [self._summary(record) for record in records[:100]]
                result[f"{key}_has_more"] = len(records) > 100
            return result

    def job_detail(self, scope: str, job_id: str, *, actor="trusted-host") -> dict:
        """Approved-scope reviewer view; record references, never source content."""
        scope = self._scope(scope)
        with privacy_guard(self.runtime_dir):
            job = self._get("job", job_id, scope)
            self.repository.record_access(scope, "review_job_selection", job_id, actor)
            fields = {
                "records",
                "outcomes",
                "policy_revision",
                "created_by",
                "approved_by",
                "cutoff",
                "held_count",
                "has_more",
                "record_cursor",
                "attempts",
                "failed_attempts",
                "record_failures",
            }
            return {
                **self._summary(job),
                **{k: v for k, v in job.items() if k in fields},
                "directory_path": scope,
                "revision": self._revision(scope),
            }

    def record_detail(self, scope: str, kind: str, record_id: str, *, actor="trusted-host") -> dict:
        """Read stored evidence through an exact-scope, audited operator surface."""
        fields_by_kind = {
            "case": set(CaseCreate.model_fields) | {
                "verification_reference", "closure_reference", "located_records",
                "manual_datasets", "failed_datasets", "located_at", "coverage_complete",
            },
            "hold": set(HoldCreate.model_fields),
            "incident": set(IncidentCreate.model_fields),
            "followup": set(FollowupCreate.model_fields) | {"evidence_reference"},
        }
        if kind not in fields_by_kind:
            raise PrivacyError("Unknown privacy evidence kind")
        scope = self._scope(scope)
        with privacy_guard(self.runtime_dir):
            record = self._get(kind, record_id, scope)
            self.repository.record_access(scope, f"review_{kind}_evidence", record_id, actor)
            fields = fields_by_kind[kind] | {"created_by", "updated_by"}
            return {
                **self._summary(record),
                **{key: value for key, value in record.items() if key in fields},
                "record_kind": kind,
                "directory_path": scope,
                "revision": self._revision(scope),
            }

    @staticmethod
    def _fields(payload, allowed, required=()):
        if (
            not isinstance(payload, dict)
            or set(payload) - set(allowed)
            or set(required) - set(payload)
        ):
            raise PrivacyError("Invalid privacy action fields")

    @staticmethod
    def _model(model, payload):
        try:
            return model.model_validate(payload)
        except ValidationError:
            raise PrivacyError(
                "Invalid privacy action fields; check types and required references"
            ) from None

    def _get(self, kind, record_id, scope):
        if not isinstance(record_id, str) or not record_id:
            raise PrivacyError("A record identifier is required")
        value = self.repository.get(kind, record_id, scope)
        if value is None:
            raise PrivacyError("Privacy record was not found in the selected scope")
        return value

    def _new(self, kind, scope, fields, actor):
        record_id = f"{kind}_{uuid4().hex}"
        timestamp = now_utc().isoformat()
        value = {
            **fields,
            "id": record_id,
            "scope": scope,
            "created_at": timestamp,
            "updated_at": timestamp,
            "created_by": actor,
        }
        self.repository.put(kind, record_id, scope, value)
        return value

    def _put(self, kind, record, actor):
        record = {**record, "updated_at": now_utc().isoformat(), "updated_by": actor}
        self.repository.put(kind, record["id"], record["scope"], record)
        return record

    def mutate(self, action: str, scope: str, payload: dict, *, expected_revision: str, actor: str):
        scope = self._scope(scope)
        if not isinstance(actor, str) or not actor.strip():
            raise PrivacyError("An accountable operator identity is required")
        if not isinstance(payload, dict):
            raise PrivacyError("Action payload must be an object")
        # One lock order across policy publication, holds, jobs and source cleanup.
        with self.operations.admin_config_mutation_lock(), privacy_guard(self.runtime_dir):
            if not expected_revision or expected_revision != self._revision(scope):
                raise PrivacyConflict("Privacy state changed; reload before taking this action")
            if action == "configure":
                result = self._configure(scope, payload)
            elif action == "retention.preview":
                result = self._retention_preview(scope, payload, actor)
            elif action in {"jobs.approve", "jobs.run"}:
                self._fields(payload, {"job_id"}, {"job_id"})
                job = self._get("job", payload["job_id"], scope)
                result = (
                    self._approve(job, actor)
                    if action.endswith("approve")
                    else self._run(job, actor)
                )
            elif action == "jobs.cancel":
                self._fields(payload, {"job_id"}, {"job_id"})
                job = self._get("job", payload["job_id"], scope)
                if job["status"] in {"local_complete", "released", "expired", "cancelled"}:
                    raise PrivacyConflict("This job has already finished")
                if job["operation"] == "export":
                    (self._export_root() / f"{job['id']}.fernet").unlink(missing_ok=True)
                result = self._summary(self._put("job", {**job, "status": "cancelled"}, actor))
            elif action == "holds.create":
                result = self._hold(scope, payload, actor)
            elif action == "holds.release":
                self._fields(payload, {"hold_id"}, {"hold_id"})
                hold = self._get("hold", payload["hold_id"], scope)
                result = self._put("hold", {**hold, "status": "released"}, actor)
            elif action == "cases.create":
                fields = self._model(CaseCreate, payload).model_dump(mode="json")
                result = self._new(
                    "case", scope, {**fields, "status": "open", "coverage_complete": False}, actor
                )
                result = self._summary(result)
            elif action == "cases.locate":
                self._fields(payload, {"case_id"}, {"case_id"})
                result = self._locate(self._get("case", payload["case_id"], scope), actor)
            elif action == "cases.preview":
                result = self._case_preview(scope, payload, actor)
            elif action == "cases.update":
                result = self._update_case(scope, payload, actor)
            elif action == "followups.create":
                fields = self._model(FollowupCreate, payload).model_dump(mode="json")
                case = self._get("case", fields["case_id"], scope)
                if case["status"].startswith("closed"):
                    raise PrivacyConflict("Closed cases cannot acquire new follow-ups")
                result = self._new("followup", scope, {**fields, "status": "pending"}, actor)
            elif action == "followups.update":
                result = self._update_followup(scope, payload, actor)
            elif action == "incidents.create":
                fields = self._model(IncidentCreate, payload).model_dump(mode="json")
                result = self._new("incident", scope, {**fields, "status": "open"}, actor)
            elif action == "incidents.update":
                result = self._update_incident(scope, payload, actor)
            elif action == "exports.release":
                result = self._release_export(scope, payload, actor)
            else:
                raise PrivacyUnsupported("This privacy action is not implemented")
            return {**self.snapshot(scope), "result": result}

    def _configure(self, scope, payload):
        self._fields(payload, {"config"}, {"config"})
        overlay = self._model(PrivacySettings, payload["config"]).model_dump(
            mode="json", exclude_unset=True
        )
        inherited = (
            self._settings(scope.rsplit("/", 1)[0]) if scope != "root" else PrivacySettings()
        )
        candidate = self._model(
            PrivacySettings, merge_privacy_settings(inherited.model_dump(mode="json"), overlay)
        )
        if scope != "root":
            root = self._settings("root")
            if set(candidate.enabled_categories) - set(root.enabled_categories):
                raise PrivacyError("Directory categories cannot exceed installation categories")
            if root.mode == "enforce" and candidate.mode != "enforce":
                raise PrivacyError("Directory policy cannot weaken installation enforcement")
            if any(candidate.retention[key].scheduled for key in overlay.get("retention", {})):
                raise PrivacyUnsupported(
                    "Scheduled source-expiry cleanup is installation-owned; use root policy"
                )
        for dataset, rule in candidate.retention.items():
            adapter = self.registry.get(dataset)
            if rule.scheduled and not adapter.descriptor.capabilities.get("retention"):
                raise PrivacyUnsupported("This dataset has no verified scheduled retention adapter")
            if dataset == "communication_artifacts" and (
                rule.days is not None or rule.trigger != "source_expiry"
            ):
                raise PrivacyError(
                    "Artifact expiry is owned by stored expires_at; do not duplicate its period"
                )
        if candidate.mode == "enforce" and self._settings(scope).mode != "enforce":
            raise PrivacyUnsupported(
                "Installation-wide enforcement cannot be activated here until all enabled outbound "
                "paths are covered. Instrumented-route policies can be tested in isolated deployments."
            )
        result = self.operations.write_directory_domain_config(
            scope, "privacy", {"privacy": overlay}
        )
        if not result.ok:
            if any(
                getattr(error, "code", "") == "feature_disabled"
                for error in getattr(result, "errors", [])
            ):
                raise PrivacyError(
                    "Directory configuration writes are disabled; enable the existing directory-scoped configuration feature first"
                )
            raise PrivacyError("Could not publish privacy configuration")
        return {"status": "published", "mode": candidate.mode}

    def _hold(self, scope, payload, actor):
        fields = self._model(HoldCreate, payload).model_dump(mode="json")
        adapter = self.registry.get(fields["dataset"])
        if not adapter.descriptor.capabilities.get("hold"):
            raise PrivacyUnsupported(
                "This dataset cannot yet enforce holds against all its cleanup paths"
            )
        visible = {ref.record_id for ref, _ in adapter.records(scope)}
        if set(fields["record_ids"]) - visible:
            raise PrivacyError("Hold selection contains missing or out-of-scope records")
        return self._summary(self._new("hold", scope, {**fields, "status": "active"}, actor))

    def _retention_preview(self, scope, payload, actor, *, scheduled=False):
        self._fields(payload, {"dataset", "limit"}, {"dataset"})
        adapter = self.registry.get(payload["dataset"])
        limit = payload.get("limit", 50)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_SELECTION:
            raise PrivacyError("Preview limit must be between 1 and 1000")
        cutoff = now_utc()
        candidates = adapter.preview(scope, cutoff=cutoff)
        held = [ref for ref in candidates if self.repository.held(ref.dataset_id, ref.record_id)]
        reserved = self.repository.unresolved_selection_ids(payload["dataset"]) if scheduled else set()
        eligible = [ref for ref in candidates if ref not in held and ref.record_id not in reserved]
        if scheduled and not eligible:
            return None
        fields = {
            "status": "preview",
            "operation": "retention",
            "dataset": payload["dataset"],
            "records": [ref.model_dump(mode="json") for ref in eligible[:limit]],
            "record_count": len(eligible[:limit]),
            "held_count": len(held),
            "has_more": len(eligible) > limit,
            "cutoff": cutoff.isoformat(),
            "policy_revision": self._policy_revision(scope),
            "outcomes": {},
            "coverage_complete": False,
        }
        job = self._new("job", scope, fields, actor)
        return {**self._summary(job), "held_count": len(held), "has_more": fields["has_more"]}

    def _locate(self, case, actor):
        if case["status"].startswith("closed"):
            raise PrivacyConflict("Closed cases cannot be changed")
        matches, manual, failures = [], [], []
        for dataset, adapter in self.registry.adapters.items():
            if not adapter.descriptor.capabilities.get("locate"):
                manual.append(dataset)
                continue
            try:
                matches.extend(adapter.locate(case["scope"], case["subject_keys"]))
            except PrivacyError:
                failures.append(dataset)
        if len(matches) > MAX_SELECTION:
            raise PrivacyUnsupported(
                "Narrow the request scope; the bounded locator found too many records"
            )
        fields = {
            **case,
            "located_records": [ref.model_dump(mode="json") for ref in matches],
            "manual_datasets": manual,
            "failed_datasets": failures,
            "coverage_complete": False,
            "located_at": now_utc().isoformat(),
        }
        self._put("case", fields, actor)
        return {
            "case_id": case["id"],
            "matches": fields["located_records"],
            "manual_datasets": manual,
            "failed_datasets": failures,
            "coverage_complete": False,
            "notice": "Exact structured identifiers only; review mirrors, free text and legacy ownership.",
        }

    @staticmethod
    def _case_authority(case):
        return revision(
            {
                key: case.get(key)
                for key in (
                    "subject_keys",
                    "instruction_ref",
                    "verification_reference",
                    "request_type",
                    "status",
                )
            }
        )

    def _case_preview(self, scope, payload, actor):
        self._fields(
            payload, {"case_id", "operation", "selections"}, {"case_id", "operation", "selections"}
        )
        case = self._get("case", payload["case_id"], scope)
        if case["status"].startswith("closed"):
            raise PrivacyConflict("Closed cases cannot create jobs")
        if (
            not str(case.get("instruction_ref", "")).strip()
            or not str(case.get("verification_reference", "")).strip()
        ):
            raise PrivacyError(
                "Record controller instructions and identity verification before execution"
            )
        operation = payload["operation"]
        if operation not in {"export", "erase", "restrict"}:
            raise PrivacyError("Unknown case operation")
        selections = payload["selections"]
        if not isinstance(selections, dict) or not selections:
            raise PrivacyError("Select explicit records from the locator result")
        located = {
            (ref["dataset_id"], ref["record_id"]): ref for ref in case.get("located_records", [])
        }
        refs = []
        for dataset, record_ids in selections.items():
            adapter = self.registry.get(dataset)
            if not adapter.descriptor.capabilities.get(operation):
                raise PrivacyUnsupported(
                    "Selected dataset does not implement this operation; record a manual follow-up"
                )
            if (
                not isinstance(record_ids, list)
                or not record_ids
                or any(not isinstance(v, str) for v in record_ids)
            ):
                raise PrivacyError("Selections must contain lists of explicit record IDs")
            for record_id in dict.fromkeys(record_ids):
                if (dataset, record_id) not in located:
                    raise PrivacyError("Selection was not located within this case scope")
                ref = RecordReference.model_validate(located[dataset, record_id])
                if adapter.check_current(scope, ref) is None:
                    raise PrivacyConflict("A located record is missing; refresh the locator")
                if operation == "erase" and self.repository.held(dataset, record_id):
                    raise PrivacyConflict(
                        "Selected record is held; resolve the hold before erasure"
                    )
                refs.append(ref.model_dump(mode="json"))
        if not 1 <= len(refs) <= MAX_SELECTION:
            raise PrivacyError("Selection must contain between 1 and 1000 records")
        job = self._new(
            "job",
            scope,
            {
                "status": "preview",
                "operation": operation,
                "case_id": case["id"],
                "records": refs,
                "record_count": len(refs),
                "outcomes": {},
                "policy_revision": self._policy_revision(scope),
                "case_authority_revision": self._case_authority(case),
                "coverage_complete": False,
            },
            actor,
        )
        return self._summary(job)

    def _approve(self, job, actor):
        if job["status"] != "preview":
            raise PrivacyConflict("Only a current preview can be approved")
        if job["policy_revision"] != self._policy_revision(job["scope"]):
            raise PrivacyConflict("Policy changed; create a fresh preview")
        if job["operation"] == "retention":
            rule = self._settings(job["scope"]).retention.get(job["dataset"])
            if rule is None or not rule.approved:
                raise PrivacyError("Publish a reviewed retention policy before approving deletion")
        if job.get("case_id"):
            case = self._get("case", job["case_id"], job["scope"])
            if self._case_authority(case) != job["case_authority_revision"]:
                raise PrivacyConflict(
                    "Controller instruction or case status changed; refresh the preview"
                )
        for raw in job["records"]:
            ref = RecordReference.model_validate(raw)
            if self.registry.get(ref.dataset_id).check_current(job["scope"], ref) is None:
                raise PrivacyConflict("Selection changed; create a fresh preview")
        return self._summary(
            self._put("job", {**job, "status": "approved", "approved_by": actor}, actor)
        )

    @staticmethod
    def _record_failures(job):
        # Older jobs counted successful batches in attempts. Only persisted
        # failure outcomes prove an unsuccessful attempt when upgrading them.
        return dict(job.get("record_failures", {
            key: 1 for key, outcome in job["outcomes"].items() if outcome == "failed"
        }))

    def _run(self, job, actor, *, limit=50, automatic=False):
        if job["status"] not in {"approved", "running", "partial_failure", "held"}:
            raise PrivacyConflict("This job is not approved for execution")
        scope = job["scope"]
        if job["policy_revision"] != self._policy_revision(scope):
            self._put("job", {**job, "status": "needs_review"}, actor)
            raise PrivacyConflict("Policy changed; create a new approved preview")
        if job.get("case_id"):
            case = self._get("case", job["case_id"], scope)
            if self._case_authority(case) != job["case_authority_revision"]:
                self._put("job", {**job, "status": "needs_review"}, actor)
                raise PrivacyConflict("Case instructions changed; create a new approved preview")
        if job["operation"] == "export":
            return self._run_export(job, actor)
        job = self._put("job", {**job, "status": "running"}, actor)
        outcomes = dict(job["outcomes"])
        failures = self._record_failures(job)
        failed_attempts = job.get("failed_attempts", int(bool(failures)))
        failed_batch = False
        examined = 0
        records = job["records"]
        cursor = job.get("record_cursor", 0)
        for offset in range(len(records)):
            index = (cursor + offset) % len(records)
            raw = records[index]
            ref = RecordReference.model_validate(raw)
            key = f"{ref.dataset_id}:{ref.record_id}"
            if outcomes.get(key) in {"deleted", "already_absent"}:
                continue
            if automatic and failures.get(key, 0) >= MAX_FAILED_ATTEMPTS:
                continue
            if examined >= limit:
                break
            examined += 1
            if self.repository.held(ref.dataset_id, ref.record_id):
                outcomes[key] = "held"
            else:
                try:
                    outcomes[key] = self.registry.get(ref.dataset_id).erase(scope, ref)
                except PrivacyConflict:
                    outcomes[key] = "changed"
                except Exception:
                    outcomes[key] = "failed"
                    failures[key] = failures.get(key, 0) + 1
                    if not failed_batch:
                        failed_attempts += 1
                        failed_batch = True
            job = self._put("job", {
                **job, "outcomes": outcomes, "record_cursor": (index + 1) % len(records),
                "record_failures": failures, "failed_attempts": failed_attempts,
            }, actor)
        states = set(outcomes.values())
        if "changed" in states:
            status = "needs_review"
        elif "failed" in states:
            status = "partial_failure"
        elif "held" in states:
            status = "held"
        elif len(outcomes) < len(job["records"]):
            status = "running"
        else:
            status = "local_complete"
        job = self._put(
            "job",
            {**job, "status": status, "outcomes": outcomes, "attempts": job.get("attempts", 0) + 1},
            actor,
        )
        return {
            **self._summary(job),
            "examined": examined,
            "outcome_counts": {
                state: list(outcomes.values()).count(state) for state in sorted(states)
            },
            "coverage_complete": False,
        }

    def _export_root(self):
        root = self.runtime_dir / "privacy-exports"
        if root.is_symlink():
            raise PrivacyError("Export storage is a symlink; manual review is required")
        return root

    def _export_cipher(self, *, create=False):
        root = self._export_root()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
        key_path = root / ".key"
        if not key_path.exists() and create:
            if any(root.glob("job_*.fernet")):
                raise PrivacyError("Export encryption key is missing; restore it before proceeding")
            try:
                fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(Fernet.generate_key())
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError:
                pass
        try:
            if key_path.is_symlink():
                raise ValueError()
            return Fernet(key_path.read_bytes())
        except (OSError, ValueError):
            raise PrivacyError("Export encryption key is unavailable or invalid") from None

    def _run_export(self, job, actor):
        records = []
        byte_count = 0
        try:
            for raw in job["records"]:
                ref = RecordReference.model_validate(raw)
                content = self.registry.get(ref.dataset_id).export_record(job["scope"], ref)
                byte_count += len(json.dumps(content).encode())
                if byte_count > MAX_EXPORT_BYTES:
                    raise PrivacyUnsupported(
                        "Export exceeds the bounded size; narrow the selection"
                    )
                records.append(
                    {"dataset": ref.dataset_id, "record_id": ref.record_id, "data": content}
                )
            content = json.dumps(
                {
                    "case_id": job["case_id"],
                    "created_at": now_utc().isoformat(),
                    "records": records,
                    "coverage_complete": False,
                    "omissions": "Allowlisted fields only. Attachment bytes, free text identity matches, "
                    "mirrors, external providers and backups require manual review.",
                },
                sort_keys=True,
            ).encode()
            if len(content) > MAX_EXPORT_BYTES:
                raise PrivacyUnsupported("Export exceeds the bounded size; narrow the selection")
            cipher = self._export_cipher(create=True)
            path = self._export_root() / f"{job['id']}.fernet"
            temporary = path.with_name(f".{job['id']}.{uuid4().hex}.fernet.tmp")
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(cipher.encrypt(content))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            expiry = now_utc() + timedelta(days=self._settings(job["scope"]).export_ttl_days)
            job = self._put(
                "job", {**job, "status": "awaiting_review", "expires_at": expiry.isoformat()}, actor
            )
        except PrivacyError:
            self._put("job", {**job, "status": "needs_review"}, actor)
            raise
        except OSError:
            self._put(
                "job",
                {
                    **job, "status": "partial_failure", "attempts": job.get("attempts", 0) + 1,
                    "failed_attempts": job.get("failed_attempts", job.get("attempts", 0)) + 1,
                },
                actor,
            )
            raise PrivacyError(
                "Encrypted export write failed; the approved job can be retried"
            ) from None
        return self._summary(job)

    def _validate_export_authority(self, job):
        case = self._get("case", job["case_id"], job["scope"])
        if (
            self._case_authority(case) != job["case_authority_revision"]
            or self._policy_revision(job["scope"]) != job["policy_revision"]
        ):
            raise PrivacyConflict("Export authority changed; prepare a fresh reviewed export")

    def export_bytes(self, scope: str, job_id: str, *, actor="trusted-host") -> bytes:
        """For authenticated privacy-export reviewers only; never guest artifacts."""
        from datetime import datetime

        scope = self._scope(scope)
        with privacy_guard(self.runtime_dir):
            job = self._get("job", job_id, scope)
            if job["operation"] != "export" or job["status"] not in {"awaiting_review", "released"}:
                raise PrivacyError("Export is not available for review")
            if datetime.fromisoformat(job["expires_at"]) <= now_utc():
                raise PrivacyError("Export has expired")
            self._validate_export_authority(job)
            path = self._export_root() / f"{job['id']}.fernet"
            try:
                if path.is_symlink() or path.stat().st_size > MAX_EXPORT_BYTES * 2:
                    raise ValueError()
                content = self._export_cipher().decrypt(path.read_bytes())
                self.repository.record_access(scope, "download_export_for_review", job_id, actor)
                return content
            except (OSError, ValueError, InvalidToken):
                raise PrivacyError("Export cannot be read securely") from None

    def _release_export(self, scope, payload, actor):
        from datetime import datetime

        self._fields(payload, {"job_id", "review_reference"}, {"job_id", "review_reference"})
        job = self._get("job", payload["job_id"], scope)
        if job["operation"] != "export" or job["status"] != "awaiting_review":
            raise PrivacyConflict("Only an export awaiting review can be released")
        if datetime.fromisoformat(job["expires_at"]) <= now_utc():
            raise PrivacyConflict("Export expired; prepare a new export")
        self._validate_export_authority(job)
        if actor == job["created_by"]:
            raise PrivacyError("Personal-content exports require a second reviewer")
        reference = payload["review_reference"]
        if not isinstance(reference, str) or not reference.strip() or len(reference) > 500:
            raise PrivacyError("A content/redaction review reference is required")
        return self._summary(
            self._put(
                "job",
                {
                    **job,
                    "status": "released",
                    "reviewed_by": actor,
                    "review_reference": reference,
                },
                actor,
            )
        )

    def _update_case(self, scope, payload, actor):
        self._fields(
            payload,
            {"case_id", "status", "instruction_ref", "verification_reference", "closure_reference"},
            {"case_id", "status"},
        )
        case = self._get("case", payload["case_id"], scope)
        if case["status"].startswith("closed"):
            raise PrivacyConflict("Closed cases cannot be edited")
        status = payload["status"]
        if status not in {
            "open",
            "awaiting_instruction",
            "fulfilled_locally",
            "closed",
            "closed_with_exceptions",
        }:
            raise PrivacyError("Unknown case status")
        for key in ("instruction_ref", "verification_reference", "closure_reference"):
            if key in payload and (not isinstance(payload[key], str) or len(payload[key]) > 500):
                raise PrivacyError("Case evidence references must be short strings")
        if status == "closed":
            raise PrivacyUnsupported(
                "Full case closure is unavailable while local coverage remains incomplete; use evidenced exceptions"
            )
        if status in {"fulfilled_locally", "closed_with_exceptions"}:
            pending = self.repository.case_has_unresolved(
                "job", scope, case["id"], {"local_complete", "released", "cancelled", "expired"}
            )
            if pending:
                raise PrivacyConflict("Case still has unresolved jobs")
        if status == "closed_with_exceptions":
            if not str(payload.get("closure_reference", "")).strip():
                raise PrivacyError("Controller-approved closure evidence is required")
            pending = self.repository.case_has_unresolved(
                "followup", scope, case["id"], {"complete", "exception"}
            )
            if pending:
                raise PrivacyConflict(
                    "Resolve or record evidenced exceptions for external follow-ups first"
                )
        changes = {key: value for key, value in payload.items() if key != "case_id"}
        return self._summary(self._put("case", {**case, **changes}, actor))

    def _update_followup(self, scope, payload, actor):
        self._fields(
            payload,
            {"followup_id", "status", "evidence_reference"},
            {"followup_id", "status", "evidence_reference"},
        )
        followup = self._get("followup", payload["followup_id"], scope)
        if self._get("case", followup["case_id"], scope)["status"].startswith("closed"):
            raise PrivacyConflict("Closed cases cannot change follow-ups")
        if payload["status"] not in {"pending", "complete", "exception"}:
            raise PrivacyError("Unknown follow-up status")
        reference = payload["evidence_reference"]
        if not isinstance(reference, str) or not reference.strip() or len(reference) > 500:
            raise PrivacyError("An outcome evidence reference is required")
        return self._summary(
            self._put(
                "followup",
                {**followup, "status": payload["status"], "evidence_reference": reference},
                actor,
            )
        )

    def _update_incident(self, scope, payload, actor):
        self._fields(
            payload,
            {"incident_id", "status", "containment_reference", "notification_reference"},
            {"incident_id", "status"},
        )
        incident = self._get("incident", payload["incident_id"], scope)
        if payload["status"] not in {"open", "contained", "closed"}:
            raise PrivacyError("Unknown incident status")
        for key in ("containment_reference", "notification_reference"):
            if key in payload and (not isinstance(payload[key], str) or len(payload[key]) > 500):
                raise PrivacyError("Incident references must be short strings")
        changes = {key: value for key, value in payload.items() if key != "incident_id"}
        if payload["status"] == "closed" and not (
            changes.get("notification_reference") or incident.get("notification_reference")
        ):
            raise PrivacyError("Record the notification decision reference before closing")
        return self._summary(self._put("incident", {**incident, **changes}, actor))

    def _cleanup_export_temporary_files(self, job, limit):
        # Every exporter holds privacy_guard. Under this same lock, a remaining
        # encrypted temporary file for a known job belongs to an interrupted run.
        root = self._export_root()
        removed = 0
        for temporary in root.glob(f".{job['id']}.*.fernet.tmp"):
            if removed >= limit:
                break
            temporary.unlink(missing_ok=True)
            removed += 1
        return removed

    def maintenance(self, *, limit=50, actor="system:privacy-maintenance"):
        from datetime import datetime

        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise PrivacyError("Maintenance limit must be between 1 and 1000")
        result = {
            "examined": 0,
            "scanned_jobs": 0,
            "jobs": [],
            "errors": 0,
            "expired_exports": 0,
            "temporary_exports_removed": 0,
        }
        with self.operations.admin_config_mutation_lock(), privacy_guard(self.runtime_dir):
            for position, job in self.repository.maintenance_page(limit):
                if result["examined"] >= limit:
                    break
                result["scanned_jobs"] += 1
                if job["operation"] == "export":
                    try:
                        removed = self._cleanup_export_temporary_files(
                            job, limit - result["examined"]
                        )
                        result["temporary_exports_removed"] += removed
                        result["examined"] += removed
                    except (OSError, PrivacyError):
                        result["errors"] += 1
                        result["examined"] += 1
                        self.repository.advance_maintenance(position)
                        continue
                    if result["examined"] >= limit:
                        self.repository.advance_maintenance(position)
                        continue
                if (
                    job.get("expires_at")
                    and datetime.fromisoformat(job["expires_at"]) <= now_utc()
                    and job["status"] != "expired"
                ):
                    try:
                        (self._export_root() / f"{job['id']}.fernet").unlink(missing_ok=True)
                        self._put("job", {**job, "status": "expired"}, actor)
                        result["expired_exports"] += 1
                    except (OSError, PrivacyError):
                        result["errors"] += 1
                    result["examined"] += 1
                    self.repository.advance_maintenance(position)
                    continue
                self.repository.advance_maintenance(position)
                if job["status"] not in {"approved", "running", "partial_failure", "held"}:
                    continue
                if (
                    job["operation"] == "export"
                    and job.get("failed_attempts", job.get("attempts", 0)) >= MAX_FAILED_ATTEMPTS
                ):
                    continue
                failures = self._record_failures(job)
                pending = [
                    f"{ref['dataset_id']}:{ref['record_id']}"
                    for ref in job["records"]
                    if job["outcomes"].get(f"{ref['dataset_id']}:{ref['record_id']}")
                    not in {"deleted", "already_absent"}
                ]
                if job["operation"] != "export" and pending and all(
                    failures.get(key, 0) >= MAX_FAILED_ATTEMPTS for key in pending
                ):
                    if job["status"] != "partial_failure":
                        self._put("job", {**job, "status": "partial_failure"}, actor)
                    continue
                try:
                    outcome = self._run(
                        job, actor, limit=limit - result["examined"], automatic=True
                    )
                    result["jobs"].append(outcome)
                    result["examined"] += max(1, outcome.get("examined", 0))
                except PrivacyError:
                    result["errors"] += 1
                    result["examined"] += 1
            # Explicitly published schedules authorize only their supported source
            # expiry; defaults do not create new deletion policies.
            if result["examined"] < limit:
                settings = self._settings("root")
                for dataset, rule in settings.retention.items():
                    if result["examined"] >= limit or not rule.approved or not rule.scheduled:
                        continue
                    try:
                        preview = self._retention_preview(
                            "root", {"dataset": dataset, "limit": limit - result["examined"]},
                            actor, scheduled=True,
                        )
                        if preview is None:
                            continue
                        job = self._get("job", preview["id"], "root")
                        self._approve(job, actor)
                        outcome = self._run(
                            self._get("job", job["id"], "root"),
                            actor,
                            limit=limit - result["examined"],
                            automatic=True,
                        )
                        result["jobs"].append(outcome)
                        result["examined"] += max(1, outcome.get("examined", 0))
                    except PrivacyError:
                        result["errors"] += 1
                        result["examined"] += 1
        return result
