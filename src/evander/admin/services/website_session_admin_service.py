from __future__ import annotations

import copy
import hashlib
import json
import os
import re

from pydantic import ValidationError

from evander.models import AuditEvent, DirectoryPath
from evander.website_credentials import WebsiteCredentialStore, credential_binding
from evander.website_sessions import (
    ManagedWebsiteSessionProfile, ManagedWebsiteSessionService, WebsiteSessionError,
    validate_environment_credentials, website_origin,
)


class WebsiteProfileRevisionConflict(ValueError):
    pass


def _revision(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class WebsiteSessionAdminService:
    def __init__(self, operations_admin, provider_config, audit) -> None:
        self.operations = operations_admin
        self.resolver = provider_config
        self.audit = audit
        self.credentials = WebsiteCredentialStore(provider_config.config.runtime_dir)
        self.broker = ManagedWebsiteSessionService(provider_config)

    def _overlay(self, scope):
        result = self.operations.read_directory_domain_config(scope, "providers")
        if not result.ok or result.data is None:
            raise ValueError("Could not read website configuration")
        return copy.deepcopy(result.data.config or {})

    def _effective(self, scope):
        return self.resolver.resolve_for_directory(scope).managed_website_sessions

    def _credential_status(self, scope, profile_id, profile):
        if profile.adapter != "json":
            return {"configured_fields": [], "managed_by": profile.adapter,
                    "updated_at": None, "owner_directory": None}
        if profile.credential_source == "stored":
            return self.credentials.status(scope, profile_id, profile)
        try:
            validate_environment_credentials(scope, profile_id, profile)
            approved = True
        except ValueError:
            approved = False
        return {"configured_fields": [k for k, env in profile.credential_fields.items()
                                      if approved and os.environ.get(env)],
                "updated_at": None, "owner_directory": None}

    def resolve_profile(self, *, directory_path: str, browser_origin: str,
                        login_origin: str) -> dict:
        """Resolve configured JSON login metadata without decrypting or using credentials."""
        scope = DirectoryPath(segments=directory_path).as_posix()
        if not all(isinstance(value, str) and value.strip() for value in (browser_origin, login_origin)):
            raise ValueError("Website profile resolution requires HTTPS browser and login origins")
        browser_origin = website_origin(browser_origin)
        login_origin = website_origin(login_origin)
        matches = [(profile_id, profile) for profile_id, profile in self._effective(scope).items()
                   if profile.enabled and profile.adapter == "json"
                   and profile.browser_origin == browser_origin]

        def unavailable(code):
            return {"ready": False, "profile_id": None, "error_code": code}

        if not matches:
            return unavailable("website_profile_not_found")
        if len(matches) != 1:
            return unavailable("website_profile_ambiguous")
        profile_id, profile = matches[0]
        if website_origin(profile.login_url) != login_origin:
            return unavailable("website_login_origin_mismatch")
        try:
            configured = set(self._credential_status(scope, profile_id, profile)["configured_fields"])
        except Exception:
            # Vault/storage failures must not publish exception details into workflow history.
            return unavailable("website_credentials_unavailable")
        if configured != set(profile.credential_fields):
            return unavailable("website_credentials_missing")
        return {"ready": True, "profile_id": profile_id, "error_code": None}

    def snapshot(self, scope: str) -> dict:
        overlay = self._overlay(scope)
        profiles = []
        for profile_id, profile in sorted(self._effective(scope).items()):
            payload = profile.model_dump(mode="json")
            status = self._credential_status(scope, profile_id, profile)
            profiles.append({"id": profile_id, "profile": payload,
                             "local": profile_id in (overlay.get("managed_website_sessions") or {}),
                             "credentials": status,
                             "last_test": self.credentials.test_status(scope, profile_id, _revision(payload))})
        return {"directory_path": scope, "revision": _revision(overlay), "profiles": profiles}

    def _check_revision(self, scope, expected_revision):
        current = self._overlay(scope)
        if not expected_revision or _revision(current) != expected_revision:
            raise WebsiteProfileRevisionConflict("Website configuration changed. Reload before saving.")
        return current

    def _write(self, scope, overlay):
        result = self.operations.write_directory_domain_config(scope, "providers", overlay)
        if not result.ok:
            raise ValueError("Could not save website configuration")

    def _audit(self, scope, profile_id, action, actor):
        self.audit.log(AuditEvent(event_type="managed_website_admin", directory_path=scope,
            actor_user_id=actor, decision="allow", result={"profile_id": profile_id, "action": action}))

    def save(self, scope: str, profile_id: str, payload: dict, actor=None) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", profile_id):
            raise ValueError("Profile ID must contain only letters, numbers, dots, underscores, or hyphens")
        try:
            profile = ManagedWebsiteSessionProfile.model_validate(payload.get("profile"))
        except ValidationError as exc:
            # ValidationError.__str__ includes input values. Never echo user input here.
            errors = [f"{'.'.join(map(str, error['loc'])) or 'profile'}: {error['msg']}"
                      for error in exc.errors(include_input=False)]
            raise ValueError("Invalid website profile: " + "; ".join(errors)) from None
        supplied = payload.get("credentials", {})
        if (not isinstance(supplied, dict) or set(supplied) - set(profile.credential_fields)
                or any(not isinstance(v, str) or not v or len(v) > 8192 for v in supplied.values())):
            raise ValueError("Credential values must be nonempty strings for configured login fields")
        if supplied and profile.credential_source != "stored":
            raise ValueError("Choose stored credentials before entering credential values")
        if profile.adapter == "json":
            validate_environment_credentials(scope, profile_id, profile)
        else:
            adapter = self.broker.adapters.get(profile.adapter)
            if adapter is None or not callable(getattr(adapter, "validate_profile", None)):
                raise ValueError("Enable the website login adapter before configuring its profile")
            if supplied or profile.credential_fields or profile.credential_source == "stored" or profile.credential_ref:
                raise ValueError("This login adapter manages its own host credential binding")
            try:
                adapter.validate_profile(scope, profile)
            except WebsiteSessionError:
                raise ValueError("Website login adapter configuration does not match its site") from None
        with self.operations.admin_config_mutation_lock():
            overlay = self._check_revision(scope, payload.get("expected_revision"))
            previous = self._effective(scope).get(profile_id)
            # The API never accepts caller-selected vault references.
            profile.credential_ref = None
            new_ref = None
            created_ref = False
            if profile.credential_source == "stored":
                values = {}
                reusable = (previous and previous.credential_source == "stored"
                            and credential_binding(previous) == credential_binding(profile))
                if not profile.enabled and not supplied:
                    # Disabling must work even when credentials have been removed or are unavailable.
                    new_ref = previous.credential_ref if reusable else None
                else:
                    if reusable:
                        values = self.credentials.read(previous.credential_ref, scope, profile_id, previous)
                    values.update(supplied)
                    if set(values) != set(profile.credential_fields) or not all(values.values()):
                        raise ValueError("Enter all credentials for a new login or changed login destination/fields")
                    new_ref = self.credentials.put(scope, profile_id, profile, values)
                    created_ref = True
                profile.credential_ref = new_ref
            overlay.setdefault("managed_website_sessions", {})[profile_id] = profile.model_dump(mode="json")
            try:
                self._write(scope, overlay)
            except Exception:
                if created_ref:
                    self.credentials.delete(new_ref, scope, profile_id)
                raise
            if previous and previous.credential_ref != new_ref:
                self.credentials.delete(previous.credential_ref, scope, profile_id)
            self._audit(scope, profile_id, "save", actor)
            return self.snapshot(scope)

    def delete(self, scope: str, profile_id: str, revision: str, *, credentials_only=False, actor=None):
        with self.operations.admin_config_mutation_lock():
            overlay = self._check_revision(scope, revision)
            local = overlay.get("managed_website_sessions") or {}
            if profile_id not in local:
                raise ValueError("Inherited profiles must be managed in their owning directory")
            previous = self._effective(scope)[profile_id]
            if credentials_only:
                if previous.credential_source != "stored":
                    raise ValueError("Host environment credentials cannot be deleted here; choose stored credentials")
                # Keep the stored source selected so removing secrets never falls back to env.
                local[profile_id]["credential_ref"] = None
            else:
                del local[profile_id]
            self._write(scope, overlay)
            self.credentials.delete(previous.credential_ref, scope, profile_id)
            self._audit(scope, profile_id, "remove_credentials" if credentials_only else "delete", actor)
            return self.snapshot(scope)

    async def test_login(self, scope: str, profile_id: str, revision: str, actor=None):
        with self.operations.admin_config_mutation_lock():
            self._check_revision(scope, revision)
            profile = self._effective(scope).get(profile_id)
            if profile is None or not profile.enabled:
                raise ValueError("Select an enabled website profile")
            profile_revision = _revision(profile.model_dump(mode="json"))
            if not self.credentials.start_test(scope, profile_id, profile_revision):
                raise ValueError("Wait 60 seconds between login tests for this profile")
        status = "ok"
        expiry = None
        try:
            if profile.adapter == "json":
                values = self.broker.credentials(scope, profile_id, profile)
                state = await self.broker._login(profile, values)
                expiry = state["expiresAt"]
            else:
                adapter = self.broker.adapters.get(profile.adapter)
                if adapter is None:
                    raise WebsiteSessionError("website_login_adapter_unavailable")
                adapter.validate_profile(scope, profile)
                status = "adapter_configured"
        except WebsiteSessionError as exc:
            status = exc.code
        with self.operations.admin_config_mutation_lock():
            current = self._effective(scope).get(profile_id)
            if current is None or _revision(current.model_dump(mode="json")) != profile_revision:
                raise WebsiteProfileRevisionConflict("Website configuration changed during the login test")
            self.credentials.finish_test(scope, profile_id, profile_revision, status, expiry)
            self._audit(scope, profile_id, "test_login:" + status, actor)
        return self.snapshot(scope)
