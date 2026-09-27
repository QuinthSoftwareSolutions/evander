from __future__ import annotations

import copy

from pydantic import ValidationError

from evander.admin.services.connector_admin_service import _deep_merge
from evander.connectors.models import ConnectorDirectoryConfig
from evander.credentials import CredentialConflict, CredentialError, CredentialStore
from evander.credentials.bindings import (
    connector_binding,
    connector_fields,
    teams_binding,
    validate_connector_values,
)
from evander.credentials.store import contains_scope, scope_path
from evander.directories import DirectoryService
from evander.models import AuditEvent


class CredentialConversionConflict(CredentialConflict):
    """Safe, actionable conversion diagnostics containing configuration IDs only."""


class CredentialAdminService:
    def __init__(
        self, *, store, provider_config, operations_admin, connector_admin, audit, directories
    ):
        self.store: CredentialStore = store
        self.resolver = provider_config
        self.operations = operations_admin
        self.connectors = connector_admin
        self.audit = audit
        self.directories = DirectoryService(directories)

    def _current_bindings(self, directory, metadata):
        ref = metadata["id"]
        if metadata["kind"] == "connector":
            config = self.resolver.resolve_connectors_for_directory(directory)
            return [
                connector_binding(pid, cid, p, c)
                for pid, p in config.auth_profiles.items()
                if p.credential_ref == ref
                for cid, c in config.connectors.items()
                if c.auth_profile == pid
            ]
        bindings = []
        for path in [directory, *(d.path for d in self.directories.list_descendants(directory))]:
            config = self.resolver.resolve_for_directory(path)
            if config.teams_delegated_credential_ref == ref:
                bindings.append(teams_binding(config, path))
        if len({b["fingerprint"] for b in bindings}) > 1:
            raise CredentialError(
                "Shared Teams consumers need matching client, tenant, authority, Graph destination and scopes"
            )
        return bindings

    def snapshot(self, directory: str) -> dict:
        scope_path(directory)
        config = self.resolver.resolve_connectors_for_directory(directory)
        teams = self.resolver.resolve_for_directory(directory)
        used = {p.credential_ref for p in config.auth_profiles.values() if p.credential_ref}
        used.add(teams.teams_delegated_credential_ref)
        records = []
        for metadata in self.store.list_metadata():
            owner = metadata["owner_directory"]
            if metadata["status"] == "deleted":
                continue
            local = owner == directory
            if not local and (metadata["id"] not in used or not contains_scope(owner, directory)):
                continue
            if not local:
                # An unapproved reference in an overlay must not disclose another
                # directory's inventory, identity, or other consumer bindings.
                allowed = any(
                    p.credential_ref == metadata["id"]
                    and self.resolver.connector_credential_owner(directory, pid) == owner
                    and connector_binding(pid, cid, p, c) in metadata["bindings"]
                    for pid, p in config.auth_profiles.items()
                    for cid, c in config.connectors.items()
                    if c.auth_profile == pid
                )
                allowed = allowed or (
                    teams.teams_delegated_credential_ref == metadata["id"]
                    and teams_binding(teams, directory) in metadata["bindings"]
                )
                if not allowed:
                    continue
                metadata = {**metadata, "bindings": []}
            if local:
                from evander.credentials.migration import config_revision

                try:
                    candidates = self._current_bindings(directory, metadata)
                    metadata = {
                        **metadata,
                        "current_bindings": candidates,
                        "bindings_revision": config_revision(candidates),
                    }
                except CredentialError as exc:
                    metadata = {**metadata, "binding_error": str(exc)}
            records.append({**metadata, "local": local})
        snapshot = self.connectors.snapshot(directory)
        choices = [
            {
                "id": pid,
                "type": p.type,
                "fields": list(connector_fields(p)),
                "credential_ref": p.credential_ref,
            }
            for pid, p in config.auth_profiles.items()
            if connector_fields(p)
        ]
        from evander.credentials.migration import _overlay, config_revision

        return {
            "directory_path": directory,
            "credentials": records,
            "teams_revision": config_revision(_overlay(self, directory)),
            "connector_profiles": choices,
            "connector_revision": snapshot["revision"],
            "teams_configured": teams.teams_backend == "delegated_graph_api",
            "store": self.store.health(),
        }

    def _audit(self, actor, directory, action, metadata):
        self.audit.log(
            AuditEvent(
                event_type="credential_admin",
                directory_path=directory,
                actor_user_id=actor,
                decision="allowed",
                result={
                    "action": action,
                    "credential_ref": metadata["id"],
                    "revision": metadata["revision"],
                },
            )
        )

    @staticmethod
    def _managed_connector_profile(profile, ref):
        managed = profile.model_dump(mode="json", exclude_none=True)
        for field in (
            "token_env", "username_env", "password_env", "client_id_env", "client_secret_env",
        ):
            managed[field] = None  # Clear inherited environment sources explicitly.
        managed.update(type=profile.type.removesuffix("_env"), credential_ref=ref)
        return managed

    def validate_connector_conversion(self, directory, pid, *, managed=None, expected_bindings=None):
        """Preview the replacement without importing values or editing children."""
        parent = self.resolver.resolve_connectors_for_directory(directory)
        profile = parent.auth_profiles.get(pid)
        if profile is None:
            raise CredentialError("Select a configured authentication profile")
        if managed is None:
            # A schema-valid preview reference; never persisted or resolved.
            managed = self._managed_connector_profile(profile, "cred_" + "0" * 32)
        parent_payload = parent.model_dump(mode="json")
        parent_payload["auth_profiles"][pid] = managed
        bindings = [
            connector_binding(pid, cid, profile, connector)
            for cid, connector in parent.connectors.items() if connector.auth_profile == pid
        ]
        if expected_bindings is not None and (
            len(bindings) != len(expected_bindings)
            or any(binding not in expected_bindings for binding in bindings)
        ):
            raise CredentialConversionConflict(
                "Connector consumers changed; reload configuration before converting credentials."
            )
        for child in self.directories.list_descendants(directory):
            conflict = CredentialConversionConflict(
                f"Cannot convert authentication profile '{pid}': descendant '{child.path}' "
                "would have an invalid profile or lose its credential binding. "
                "Migrate that child's credential first, or give it a complete local "
                "authentication profile with credential_ref set to null, then retry."
            )
            result = self.operations.compute_directory_effective_config(child.path, "connectors")
            if not result.ok or result.data is None:
                raise conflict
            candidate = copy.deepcopy(parent_payload)
            for layer in result.data.resolution.applied_layers:
                if layer.directory_path.startswith(directory + "/"):
                    candidate = _deep_merge(candidate, layer.overlay)
            try:
                effective = ConnectorDirectoryConfig.model_validate(candidate)
            except ValidationError:
                raise conflict from None
            child_profile = effective.auth_profiles.get(pid)
            if child_profile and child_profile.credential_ref == managed["credential_ref"]:
                if any(
                    connector_binding(pid, cid, child_profile, connector) not in bindings
                    for cid, connector in effective.connectors.items()
                    if connector.auth_profile == pid and not connector.disabled
                ):
                    raise conflict

    def create_connector(
        self, directory: str, payload: dict, actor=None, *, recovery=False
    ) -> dict:
        snapshot = self.connectors.snapshot(directory)
        expected = payload.get("expected_config_revision")
        if not expected or expected != snapshot["revision"]:
            raise CredentialConflict("Connector configuration changed; reload before saving")
        config = self.resolver.resolve_connectors_for_directory(directory)
        pid = payload.get("profile_id")
        profile = config.auth_profiles.get(pid) if isinstance(pid, str) else None
        if profile is None:
            raise CredentialError("Select a configured authentication profile")
        if profile.credential_ref:
            prior = self.store.metadata(profile.credential_ref)
            if prior["owner_directory"] == directory and prior["status"] != "deleted":
                raise CredentialError("Replace existing managed credentials separately")
        values = payload.get("values")
        validate_connector_values(profile, values)
        bindings = [
            connector_binding(pid, cid, profile, c)
            for cid, c in config.connectors.items()
            if c.auth_profile == pid
        ]
        if not bindings:
            raise CredentialError("Authentication profile must have a configured connector")
        self.validate_connector_conversion(directory, pid, expected_bindings=bindings)
        metadata = self.store.create(
            owner=directory,
            kind="connector",
            label=payload.get("label", pid),
            bindings=bindings,
            values=values,
            expires_at=payload.get("expires_at"),
        )
        overlay = copy.deepcopy(snapshot["config"])
        managed = self._managed_connector_profile(profile, metadata["id"])
        overlay.setdefault("auth_profiles", {})[pid] = managed
        recovery_path = None
        try:
            if recovery:
                recovery_path = self.store.backup("connector-migration-recovery")
            self.connectors.save(
                directory, expected_revision=expected, config_payload=overlay,
                preflight=lambda: self.validate_connector_conversion(
                    directory, pid, managed=managed, expected_bindings=bindings
                ),
            )
        except Exception:
            # Never leave an active unbound credential when the config CAS fails.
            self.store.update(
                metadata["id"],
                owner=directory,
                expected_revision=metadata["revision"],
                status="deleted",
            )
            raise
        self._audit(actor, directory, "create", metadata)
        return {
            **self.snapshot(directory),
            **({"recovery_snapshot": str(recovery_path)} if recovery_path else {}),
        }

    def prepare_teams(self, directory: str, payload: dict, actor=None) -> dict:
        # Use the same migration machinery as the CLI; only a signed-out identity
        # can be prepared here. Existing credentials use the maintenance migration.
        from evander.credentials.migration import prepare_teams

        metadata = prepare_teams(self, directory, payload)
        self._audit(actor, directory, "prepare_teams", metadata)
        return self.snapshot(directory)

    def update(self, directory: str, ref: str, payload: dict, actor=None) -> dict:
        metadata = self.store.metadata(ref)
        if metadata["owner_directory"] != directory:
            raise CredentialError("Inherited credentials must be managed in their owning directory")
        action = payload.get("action")
        kwargs = {}
        if action == "replace":
            if metadata["kind"] != "connector":
                raise CredentialError("Use Teams sign-in to replace Teams credentials")
            profiles = self.resolver.resolve_connectors_for_directory(directory).auth_profiles
            bound = [p for p in profiles.values() if p.credential_ref == ref]
            if not bound:
                raise CredentialError("Credential has no configured authentication profile")
            for p in bound:
                validate_connector_values(p, payload.get("values"))
            kwargs = {"values": payload["values"], "expires_at": payload.get("expires_at")}
        elif action in {"disable", "enable", "delete"}:
            kwargs = {
                "status": {"disable": "disabled", "enable": "active", "delete": "deleted"}[action]
            }
        elif action == "approve_bindings":
            from evander.credentials.migration import config_revision

            kwargs = {"bindings": self._current_bindings(directory, metadata)}
            if payload.get("expected_bindings_revision") != config_revision(kwargs["bindings"]):
                raise CredentialConflict("Consumer configuration changed; reload before approving")
            if not kwargs["bindings"]:
                raise CredentialError("Credential has no configured consumer")
            if metadata["kind"] == "teams_oauth":
                # New client/tenant/scope policy needs fresh consent; never reuse
                # tokens acquired under the previous identity binding.
                kwargs["values"] = {}
        else:
            raise CredentialError("Unsupported credential action")
        updated = self.store.update(
            ref, owner=directory, expected_revision=payload.get("expected_revision"), **kwargs
        )
        self._audit(actor, directory, action, updated)
        return self.snapshot(directory)
