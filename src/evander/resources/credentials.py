"""Resolve configured host credentials with an exact destination and purpose binding."""

import os

from evander.credentials.store import CredentialStore, binding_fingerprint

from .models import Credential, ResourceError


def binding(addon_id, connection_id, connection, purpose):
    public = {
        k: v for k, v in connection.items() if k not in {"credentials", "admin", "read", "write"}
    }
    return {
        "consumer": "resource",
        "addon_id": addon_id,
        "connection_id": connection_id,
        "purpose": purpose,
        "fingerprint": binding_fingerprint(public),
    }


class Credentials:
    def __init__(self, runtime_dir, addon_id, store=None):
        self.store = store or CredentialStore(runtime_dir)
        self.addon_id = addon_id

    def resolve(self, connection_id, connection, purpose, directory):
        source = connection.get("credentials", {}).get(purpose)
        if source is None:
            raise ResourceError("Connection has no credentials for this operation")
        config = Credential.model_validate(source)
        if config.credential_ref:
            values, _ = self.store.resolve(
                config.credential_ref,
                directory=directory,
                kind="resource",
                binding=binding(self.addon_id, connection_id, connection, purpose),
            )
        else:
            values = {
                "username": config.username or os.environ.get(config.username_env or "", ""),
                "password": os.environ.get(config.password_env, ""),
            }
        if set(values) != {"username", "password"} or not all(
            isinstance(v, str) and v for v in values.values()
        ):
            raise ResourceError("Host credentials are unavailable")
        return values

    def create(self, connection_id, connection, purpose, directory, values):
        if set(values) != {"username", "password"} or not all(
            isinstance(v, str) and v for v in values.values()
        ):
            raise ResourceError("Username and password are required")
        return self.store.create(
            owner=directory,
            kind="resource",
            label=f"{self.addon_id}/{connection_id}/{purpose}",
            bindings=[binding(self.addon_id, connection_id, connection, purpose)],
            values=values,
        )
