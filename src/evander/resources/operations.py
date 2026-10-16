"""At-most-once dispatch for writes whose provider acknowledgement can be lost."""

import hashlib
import json

from .models import Conflict, ResourceError, Uncertain


def perform(store, owner, resource_id, operation, arguments, request_id, invoke, *, read=False):
    if read:
        return invoke()
    if not request_id or len(request_id) > 128:
        raise ResourceError("Mutations require a request_id of at most 128 characters")

    def digest(value):
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    key = digest([owner.identity(), request_id])
    fingerprint = digest([resource_id, operation, arguments])
    with store.locked():
        prior = store.operation(key)
        if prior:
            if prior[0] != fingerprint:
                raise Conflict("request_id is bound to another operation")
            return prior[1]
        result = {
            "id": key,
            "directory": owner.directory,
            "operation": operation,
            "resource_id": resource_id,
            "state": "uncertain",
            "revision": 1,
        }
        store.put_operation(key, fingerprint, result)
        try:
            value = invoke()
        except Exception as exc:
            if isinstance(exc, Uncertain) and exc.progress is not None:
                result["progress"] = exc.progress
                store.put_operation(key, fingerprint, result)
            store.audit(owner.key, operation + ":uncertain", resource_id)
            return result
        result.update(state="completed", result=value, revision=2)
        store.put_operation(key, fingerprint, result)
        store.audit(owner.key, operation, resource_id)
        return result
