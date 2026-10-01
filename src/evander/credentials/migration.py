from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from evander.directories import DirectoryService
from evander.messaging.teams_auth import (
    TeamsAuthCredentials,
    TeamsAuthProtocolError,
    decode_legacy_teams_credential_envelope,
    legacy_teams_credential_key_path,
)

from .bindings import connector_fields, teams_binding
from .store import (
    CredentialConflict,
    CredentialError,
    CredentialStore,
    canonical,
    fsync_directory,
    protected_read,
)

if TYPE_CHECKING:
    from evander.admin.services.credential_admin_service import CredentialAdminService


def config_revision(payload: object) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()


def _overlay(admin, directory: str) -> dict:
    result = admin.operations.read_directory_domain_config(directory, "providers")
    if not result.ok or result.data is None:
        raise CredentialError("Could not read Teams configuration")
    return copy.deepcopy(result.data.config or {})


def _write(admin, directory, payload):
    result = admin.operations.write_directory_domain_config(directory, "providers", payload)
    if not result.ok:
        raise CredentialError("Could not save Teams credential reference")


def _empty_legacy_teams_source(source: Path) -> bool:
    """Recognize signed-out storage without initializing keys or changing data."""
    raw = protected_read(source, limit=4 * 1024 * 1024, legacy_permissions=True)
    try:
        payload = json.loads(raw)
        decoded = decode_legacy_teams_credential_envelope(
            payload, key_path=legacy_teams_credential_key_path(source)
        )
        if decoded is not None:
            _, payload = decoded
        return isinstance(payload, dict) and not payload
    except (TeamsAuthProtocolError, ValueError, TypeError):
        raise CredentialError("Teams source format or identity mapping is invalid") from None


def prepare_teams(admin, directory, payload) -> dict:
    with admin.operations.admin_config_mutation_lock():
        config = admin.resolver.resolve_for_directory(directory)
        overlay = _overlay(admin, directory)
        if payload.get("expected_config_revision") != config_revision(overlay):
            raise CredentialConflict("Teams configuration changed; reload before saving")
        if config.teams_backend != "delegated_graph_api":
            raise CredentialError("Select an unmigrated delegated Teams identity")
        if config.teams_delegated_credential_ref:
            previous = admin.store.metadata(config.teams_delegated_credential_ref)
            if previous["owner_directory"] == directory and previous["status"] != "deleted":
                raise CredentialError("Use the existing managed Teams credential")
        elif (
            Path(config.teams_delegated_credential_store_path).exists()
            and not _empty_legacy_teams_source(Path(config.teams_delegated_credential_store_path))
        ):
            raise CredentialError(
                "Existing Teams storage requires the maintenance migration command"
            )
        metadata = admin.store.create(
            owner=directory,
            kind="teams_oauth",
            label=config.teams_delegated_identity_id,
            values={},
            bindings=[teams_binding(config, directory)],
        )
        overlay["teams_delegated_credential_ref"] = metadata["id"]
        try:
            _write(admin, directory, overlay)
        except Exception:
            admin.store.update(
                metadata["id"], expected_revision=metadata["revision"], status="deleted"
            )
            raise
        return metadata


def encrypted_backup(store: CredentialStore, name: str) -> Path:
    """SQLite backup includes committed ciphertext and metadata, never plaintext."""
    return store.backup(name)


def _teams_groups(runtime) -> list[dict]:
    admin = runtime.credential_admin
    directories = DirectoryService(runtime.directory_store)
    paths = ["root", *(d.path for d in directories.list_descendants("root"))]
    groups: dict[tuple[str, str], dict] = {}
    for directory in paths:
        config = admin.resolver.resolve_for_directory(directory)
        if config.teams_backend != "delegated_graph_api":
            continue
        key = (
            str(config.teams_delegated_credential_store_path),
            config.teams_delegated_identity_id,
        )
        group = groups.setdefault(key, {"source": key[0], "identity": key[1], "consumers": []})
        group["consumers"].append(
            {
                "directory": directory,
                "config": config,
                "overlay": _overlay(admin, directory),
                "binding": teams_binding(config, directory),
            }
        )
    return list(groups.values())


def migrate_teams(runtime, *, apply: bool = False, maintenance: bool = False) -> dict:
    if apply and not maintenance:
        raise CredentialError(
            "Stop Teams consumers and pass --maintenance before applying migration"
        )
    admin = runtime.credential_admin
    return _TeamsCredentialMigration(admin, _teams_groups(runtime)).run(apply=apply)


class _TeamsCredentialMigration:
    """Inspect one migration; import, snapshot, switch, and retire under one lock."""

    result: dict[str, Any]
    sources: dict[str, tuple[bytes, dict]]
    transitional_keys: dict[str, Path]
    switched_ancestors: set[tuple[str, str]]

    def __init__(self, admin: CredentialAdminService, groups: list[dict]) -> None:
        self.admin = admin
        self.groups = groups

    def run(self, *, apply: bool) -> dict:
        self._capture_migration_markers()
        self.result = {"family": "teams", "applied": False, "identities": [], "conflicts": []}
        # Capture both config and source identity before acquiring the mutation lock.
        self.sources: dict[str, tuple[bytes, dict]] = {}
        self.transitional_keys: dict[str, Path] = {}
        for group in self.groups:
            self._inspect_identity(group)
        if not apply or self.result["conflicts"]:
            return self.result
        if not self.admin.store.health()["available"]:
            raise CredentialError("Initialize credential storage before migration")
        if not self.sources and all(group["already"] for group in self.groups):
            self._resolve_existing_bindings()
            self.result["unchanged"] = True
            return self.result
        with self.admin.operations.admin_config_mutation_lock():
            self._revalidate_inputs()
            self._import_credentials()
            # Snapshot imported encrypted values before switching any configuration.
            self.result["recovery_snapshot"] = str(
                encrypted_backup(self.admin.store, "teams-migration-recovery")
            )
            self._switch_references()
            self._retire_sources()
        self.result["applied"] = True
        return self.result

    def _capture_migration_markers(self) -> None:
        for group in self.groups:
            marker_id = (
                "teams:"
                + hashlib.sha256(canonical([group["source"], group["identity"]])).hexdigest()
            )
            group.update(marker_id=marker_id, marker=self.admin.store.migration(marker_id))
        # All imports precede reference switches. An interrupted parent switch can
        # temporarily flow into descendants whose own imported record is still pending.
        self.switched_ancestors = {
            (c["directory"], g["marker"]["credential_ref"])
            for g in self.groups
            if g["marker"]
            for c in g["consumers"]
            if c["overlay"].get("teams_delegated_credential_ref") == g["marker"]["credential_ref"]
        }

    def _inherited_migration_switch(self, consumer: dict) -> bool:
        if consumer["overlay"].get("teams_delegated_credential_ref"):
            return False
        return any(
            consumer["directory"].startswith(parent + "/")
            and consumer["config"].teams_delegated_credential_ref == ref
            for parent, ref in self.switched_ancestors
        )

    def _inspect_identity(self, group: dict) -> None:
        consumers = group["consumers"]
        refs = {c["config"].teams_delegated_credential_ref for c in consumers}
        source = Path(group["source"])
        marker = group["marker"]
        ref = marker["credential_ref"] if marker else (next(iter(refs)) if len(refs) == 1 else None)
        imported = self.admin.store.metadata(ref) if marker else None
        already = bool(ref) and all(
            ref == c["config"].teams_delegated_credential_ref for c in consumers
        )
        self.result["identities"].append(
            {
                "identity": group["identity"],
                "directories": [c["directory"] for c in consumers],
                "migrated": already,
                "owner_directory": consumers[0]["directory"] if len(consumers) == 1 else "root",
            }
        )
        group["already"] = already
        fingerprints = {c["binding"]["fingerprint"] for c in consumers}
        if (
            len(fingerprints) != 1
            or (
                marker
                and any(
                    c["config"].teams_delegated_credential_ref
                    and c["config"].teams_delegated_credential_ref != marker["credential_ref"]
                    and not self._inherited_migration_switch(c)
                    for c in consumers
                )
            )
            or (imported and any(c["binding"] not in imported["bindings"] for c in consumers))
            or (any(refs) and not already and not marker)
        ):
            self.result["conflicts"].append(
                "Shared Teams identity has conflicting configuration or references"
            )
            return
        if not self._capture_source(group, source):
            return
        payload = self.sources.get(group["source"], (b"", {}))[1]
        group["source_digest"] = hashlib.sha256(
            self.sources.get(group["source"], (b"", {}))[0]
        ).hexdigest()
        values = payload.get(group["identity"], {})
        if payload and group["identity"] not in payload:
            self.result["conflicts"].append("Teams source does not contain the configured identity")
            return
        if already and not marker:
            # References prepared independently are not evidence that this legacy
            # source was imported. Keep it for explicit operator reconciliation.
            group["ref"] = ref
            group["retain_source"] = True
            return
        if marker:
            if source.exists() and marker.get("source_digest") != group["source_digest"]:
                self.result["conflicts"].append(
                    "Teams source changed after import; reconcile before retirement"
                )
                return
            group["ref"] = marker["credential_ref"]
        else:
            if values:
                try:
                    creds = TeamsAuthCredentials.model_validate(values)
                    cfg = consumers[0]["config"]
                    if creds.client_id != cfg.teams_delegated_client_id:
                        raise ValueError
                    if cfg.teams_delegated_tenant_id not in {
                        "common",
                        "organizations",
                        "consumers",
                        creds.tenant_id,
                    }:
                        raise ValueError
                    values = creds.model_dump(mode="json")
                except ValueError:
                    self.result["conflicts"].append(
                        "Teams token identity/client/tenant does not match its configuration"
                    )
                    return
            group["values"] = values

    def _capture_source(self, group: dict, source: Path) -> bool:
        if source.exists() and group["source"] not in self.sources:
            # Legacy files may predate restrictive modes. Never expose their data;
            # maintenance first makes the source private, then uses no-follow reads.
            if source.is_symlink():
                self.result["conflicts"].append("Teams source must not be a symlink")
                return False
            raw = protected_read(source, limit=4 * 1024 * 1024, legacy_permissions=True)
            if len(raw) > 4 * 1024 * 1024:
                raise CredentialError("Teams source exceeds migration size limit")
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError
                identities = {
                    item["identity"] for item in self.groups if item["source"] == group["source"]
                }
                decoded = decode_legacy_teams_credential_envelope(
                    payload,
                    key_path=legacy_teams_credential_key_path(source),
                )
                if decoded is not None:
                    kind, credentials = decoded
                    if kind == "single":
                        if len(identities) != 1:
                            raise ValueError
                        payload = {next(iter(identities)): credentials}
                    else:
                        payload = credentials
                    self.transitional_keys[group["source"]] = legacy_teams_credential_key_path(
                        source
                    )
                # Support the original single-identity credential document.
                if "access_token" in payload:
                    if len(identities) != 1:
                        raise ValueError
                    payload = {group["identity"]: payload}
                self.sources[group["source"]] = (raw, payload)
            except (TeamsAuthProtocolError, ValueError, TypeError):
                raise CredentialError(
                    "Teams source format or identity mapping is invalid"
                ) from None
        return True

    def _resolve_existing_bindings(self) -> None:
        for group in self.groups:
            for consumer in group["consumers"]:
                self.admin.store.resolve(
                    group["ref"],
                    directory=consumer["directory"],
                    binding=consumer["binding"],
                    kind="teams_oauth",
                )

    def _revalidate_inputs(self) -> None:
        for group in self.groups:
            for c in group["consumers"]:
                if config_revision(_overlay(self.admin, c["directory"])) != config_revision(
                    c["overlay"]
                ):
                    raise CredentialConflict("Teams configuration changed during migration")
        for source, (raw, _) in self.sources.items():
            if protected_read(Path(source), limit=4 * 1024 * 1024, legacy_permissions=True) != raw:
                raise CredentialConflict("Teams source changed; stop all consumers before retrying")
            os.chmod(source, 0o600, follow_symlinks=False)

    def _import_credentials(self) -> None:
        for group in self.groups:
            if group["already"]:
                continue
            if not group.get("ref"):
                consumers = group["consumers"]
                values = group["values"]
                metadata = self.admin.store.create(
                    owner=consumers[0]["directory"] if len(consumers) == 1 else "root",
                    kind="teams_oauth",
                    label=group["identity"],
                    values=values,
                    bindings=[c["binding"] for c in consumers],
                    expires_at=TeamsAuthCredentials.model_validate(values).expires_at.timestamp()
                    if values
                    else None,
                    migration_id=group["marker_id"],
                    source_digest=group["source_digest"],
                )
                group["ref"] = metadata["id"]

    def _switch_references(self) -> None:
        for group in self.groups:
            for c in group["consumers"]:
                # Verify authenticated ciphertext and its original binding first.
                self.admin.store.resolve(
                    group["ref"], directory=c["directory"], binding=c["binding"], kind="teams_oauth"
                )
                if group["already"]:
                    continue
                overlay = _overlay(self.admin, c["directory"])
                overlay["teams_delegated_credential_ref"] = group["ref"]
                _write(self.admin, c["directory"], overlay)
            if not group.get("retain_source"):
                self.admin.store.record_migration(
                    group["marker_id"],
                    {
                        "credential_ref": group["ref"],
                        "phase": "active",
                        "source_digest": (group["marker"] or {}).get(
                            "source_digest", group["source_digest"]
                        ),
                    },
                )

    def _retire_sources(self) -> None:
        for source, (raw, payload) in self.sources.items():
            # Never discard an unconfigured identity from a shared legacy file.
            migrated_ids = {g["identity"] for g in self.groups if g["source"] == source}
            if set(payload) - migrated_ids or any(
                g.get("retain_source") for g in self.groups if g["source"] == source
            ):
                self.result.setdefault("retained_sources", 0)
                self.result["retained_sources"] += 1
                continue
            if protected_read(Path(source), limit=4 * 1024 * 1024) != raw:
                raise CredentialConflict("Teams source changed before retirement")
            Path(source).unlink()
            fsync_directory(Path(source).parent)
            key_path = self.transitional_keys.get(source)
            if key_path is not None:
                try:
                    key_path.unlink(missing_ok=True)
                    fsync_directory(key_path.parent)
                except OSError:
                    self.result.setdefault("retained_legacy_keys", []).append(str(key_path))


def migrate_connector(runtime, *, directory: str, profile_id: str, apply=False) -> dict:
    from evander.admin.services.credential_admin_service import CredentialConversionConflict

    admin = runtime.credential_admin
    config = admin.resolver.resolve_connectors_for_directory(directory)
    profile = config.auth_profiles.get(profile_id)
    if profile is None or not connector_fields(profile):
        raise CredentialError("Select a configured authentication profile")
    consumers = [cid for cid, c in config.connectors.items() if c.auth_profile == profile_id]
    report = {
        "family": "connector",
        "profile_id": profile_id,
        "directory": directory,
        "consumers": consumers,
        "migrated": bool(profile.credential_ref),
        "applied": False,
    }
    if profile.credential_ref:
        return report
    try:
        admin.validate_connector_conversion(directory, profile_id)
    except CredentialConversionConflict as exc:
        report["conflicts"] = [str(exc)]
        return report
    if not apply:
        return report
    names = {
        "token": profile.token_env,
        "username": profile.username_env,
        "password": profile.password_env,
        "client_id": profile.client_id_env,
        "client_secret": profile.client_secret_env,
    }
    values = {field: os.environ.get(names[field] or "", "") for field in connector_fields(profile)}
    saved = admin.create_connector(
        directory,
        {
            "profile_id": profile_id,
            "label": profile_id,
            "values": values,
            "expected_config_revision": admin.connectors.snapshot(directory)["revision"],
        },
        actor="operator-cli",
        recovery=True,
    )
    report["recovery_snapshot"] = saved["recovery_snapshot"]
    report["applied"] = True
    report["environment_cleanup"] = (
        "Retain source values until all other consumers have been checked"
    )
    return report
