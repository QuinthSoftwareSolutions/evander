from __future__ import annotations

from contextlib import contextmanager

from evander_teams_graph_addon.auth import (
    JsonTeamsIdentityCredentialStore,
    TeamsAuthCredentials,
    TeamsAuthenticationRequiredError,
)

from evander.credentials.bindings import teams_binding
from evander.credentials.store import CredentialError, CredentialStore, contains_scope


class ManagedTeamsCredentialStore:
    """Adapter preserving Teams semantics with process-wide locking and CAS."""

    fresh_reads = True

    def __init__(self, config, directory: str, *, store: CredentialStore | None = None):
        self.store = store or CredentialStore(config.runtime_dir)
        self.ref = config.teams_delegated_credential_ref
        self.directory = directory
        self.consumer_directory = directory
        self.binding = teams_binding(config, directory)
        self.revision: str | None = None

    @contextmanager
    def refresh_lock(self):
        try:
            with self.store.lock(self.ref):
                yield
        except CredentialError:
            raise TeamsAuthenticationRequiredError(
                "Managed Teams credentials are unavailable"
            ) from None

    def load(self) -> TeamsAuthCredentials | None:
        try:
            values, metadata = self.store.resolve(
                self.ref,
                directory=self.consumer_directory,
                binding=self.binding,
                kind="teams_oauth",
            )
            self.revision = metadata["revision"]
            return TeamsAuthCredentials.model_validate(values) if values else None
        except (CredentialError, ValueError):
            raise TeamsAuthenticationRequiredError(
                "Managed Teams credentials are unavailable"
            ) from None

    def require_owner(self):
        try:
            if self.store.metadata(self.ref)["owner_directory"] != self.directory:
                raise CredentialError("Credential has another owner")
        except CredentialError:
            raise TeamsAuthenticationRequiredError(
                "Manage Teams sign-in in the credential's owning directory"
            ) from None

    def save(self, credentials: TeamsAuthCredentials, *, refresh=False) -> None:
        if self.revision is None:
            self.load()
        try:
            metadata = self.store.update(
                self.ref,
                expected_revision=self.revision,
                values=credentials.model_dump(mode="json"),
                expires_at=credentials.expires_at.timestamp(),
                require_active=True,
                owner=None if refresh else self.directory,
            )
            self.revision = metadata["revision"]
        except CredentialError:
            raise TeamsAuthenticationRequiredError(
                "Teams credentials changed; sign in or retry"
            ) from None

    def clear(self, *, revoked=False) -> None:
        # A late revoked-token response must not clear a newer login/refresh.
        if self.revision is None:
            self.load()
        try:
            metadata = self.store.update(
                self.ref,
                expected_revision=self.revision,
                values={},
                require_active=True,
                owner=None if revoked else self.directory,
            )
            self.revision = metadata["revision"]
        except CredentialError:
            raise TeamsAuthenticationRequiredError(
                "Teams credentials changed; reload status"
            ) from None


def teams_credential_store(config, directory: str):
    if config.teams_delegated_credential_ref:
        return ManagedTeamsCredentialStore(config, directory)
    return JsonTeamsIdentityCredentialStore(
        config.teams_delegated_credential_store_path, config.teams_delegated_identity_id
    )


def teams_owner_credential_store(config, directory: str, ref: str):
    """Address an owned identity using its approved policy, independently of owner config."""
    try:
        store = CredentialStore(config.runtime_dir)
        metadata = store.metadata(ref)
        bindings = metadata["bindings"]
        if (
            metadata["owner_directory"] != directory
            or metadata["kind"] != "teams_oauth"
            or metadata["status"] != "active"
            or not bindings
            or len({b["fingerprint"] for b in bindings}) != 1
        ):
            raise CredentialError("Managed Teams identity is not available to this owner")
        binding = bindings[0]
        if binding["consumer"] != "teams" or not contains_scope(directory, binding["directory"]):
            raise CredentialError("Invalid Teams consumer")
        # Construct only from the approved record; changing any live consumer's
        # policy must not redirect this owner's device-code or token requests.
        config = type(config).model_validate(
            {
                **config.model_dump(),
                "teams_backend": "delegated_graph_api",
                "teams_delegated_credential_ref": ref,
                "teams_delegated_identity_id": binding["identity_id"],
                "teams_delegated_client_id": binding["client_id"],
                "teams_delegated_tenant_id": binding["tenant"],
                "teams_delegated_authority_base_url": binding["authority"],
                "teams_delegated_scopes": binding["scopes"],
                "teams_graph_api_base_url": binding["destination"],
            }
        )
        if teams_binding(config, binding["directory"]) != binding:
            raise CredentialError("Teams binding needs owner approval")
        adapter = ManagedTeamsCredentialStore(config, directory, store=store)
        adapter.consumer_directory = binding["directory"]
        adapter.binding = binding
        adapter.require_owner()
        return config, adapter
    except (CredentialError, KeyError, TypeError, ValueError):
        raise TeamsAuthenticationRequiredError(
            "Managed Teams identity is unavailable; check ownership, status and approved consumers"
        ) from None
