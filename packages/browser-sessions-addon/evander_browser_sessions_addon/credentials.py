"""Browser credentials are platform-owned, scoped and bound to a complete connection."""

from evander.credentials import CredentialError, CredentialStore
from evander.credentials.bearer import resolve_bearer

from .models import BrowserError, WorkerConnection, origin


def connection_binding(purpose, base_url, *, worker_id=None, connection=None):
    origin(base_url)
    value = {
        "consumer": "browser-sessions-addon",
        "purpose": purpose,
        "destination": base_url.rstrip("/"),
    }
    if purpose == "worker":
        if not worker_id or connection is None:
            raise BrowserError("Browser worker identity is unavailable")
        connection = WorkerConnection.model_validate(connection)
        value.update(
            worker_id=worker_id,
            parent_worker_url=connection.parent_worker_url,
            relay_url=connection.relay_url,
        )
    elif purpose not in {"relay", "voice"}:
        raise BrowserError("Unknown browser credential purpose")
    return value


class Credentials:
    def __init__(self, runtime_dir, *, store=None):
        self.store = store or CredentialStore(runtime_dir)

    def reference(self, directory, purpose, base_url, credential_ref, **kwargs):
        if not credential_ref:
            raise BrowserError(
                "Configure managed browser credentials before using this connection",
                "credential_unavailable",
                409,
            )
        binding = connection_binding(purpose, base_url, **kwargs)
        try:
            self.store.authorize(
                self.store.metadata(credential_ref),
                directory=directory,
                binding=binding,
                kind="resource",
            )
        except CredentialError:
            raise BrowserError(
                "Browser connection is not authorized", "credential_unavailable", 409
            ) from None
        return {"credential_ref": credential_ref, "directory": directory, "binding": binding}

    def token(self, directory, purpose, base_url, credential_ref, **kwargs):
        auth = self.reference(directory, purpose, base_url, credential_ref, **kwargs)
        try:
            return resolve_bearer(
                self.store,
                auth["credential_ref"],
                directory=directory,
                binding=auth["binding"],
                url=base_url.rstrip("/") + "/",
            )
        except CredentialError:
            raise BrowserError(
                "Browser credentials are unavailable", "credential_unavailable", 503
            ) from None

    def create(self, directory, purpose, base_url, token, **kwargs):
        if (
            not isinstance(token, str)
            or not 1 <= len(token) <= 8192
            or "\r" in token
            or "\n" in token
        ):
            raise BrowserError("Enter a nonempty single-line credential")
        return self.store.create(
            owner=directory,
            kind="resource",
            label="Browser " + purpose,
            bindings=[connection_binding(purpose, base_url, **kwargs)],
            values={"token": token},
        )


def connection_settings(settings):
    """Only owners may change these credential-bearing connection selections."""
    return {
        "public_base_url": settings.public_base_url,
        "relay_url": settings.relay_url,
        "relay_credential_ref": settings.relay_credential_ref,
        "workers": {key: value.model_dump() for key, value in settings.workers.items()},
        "voice_url": settings.voice.api_base_url,
        "voice_credential_ref": settings.voice.credential_ref,
    }


def configure_credential(service, directory, payload, actor):
    """Publish a replacement reference with CAS; never mutate a pinned secret."""
    revision, settings = service.store.settings(directory)
    if (
        type(payload.get("expected_revision")) is not int
        or payload["expected_revision"] != revision
    ):
        raise BrowserError("Settings changed; reload before saving", "revision_conflict", 409)
    purpose = payload.get("purpose")
    kwargs = {}
    if purpose == "worker":
        worker_id = payload.get("worker_id")
        connection = settings.workers.get(worker_id)
        if connection is None:
            raise BrowserError("Select a configured browser worker")
        url = connection.base_url
        kwargs = {"worker_id": worker_id, "connection": connection}
    elif purpose == "relay":
        url = settings.relay_url
    elif purpose == "voice":
        url = settings.voice.api_base_url
    else:
        raise BrowserError("Select a browser credential purpose")
    metadata = service.credentials.create(directory, purpose, url, payload.get("token"), **kwargs)
    try:
        if purpose == "worker":
            connection.credential_ref = metadata["id"]
        elif purpose == "relay":
            settings.relay_credential_ref = metadata["id"]
        else:
            settings.voice.credential_ref = metadata["id"]
        revision = service.store.save_settings(directory, revision, settings)
    except Exception:
        service.credentials.store.update(
            metadata["id"],
            owner=directory,
            expected_revision=metadata["revision"],
            status="disabled",
        )
        raise
    service.store.audit(
        "settings:" + directory,
        actor,
        "credential_configured",
        {
            "purpose": purpose,
            "credential_ref": metadata["id"],
        },
    )
    return revision, settings
