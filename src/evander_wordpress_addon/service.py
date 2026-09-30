"""One recoverable change engine for CMS, browser and immutable code releases."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import time

from evander.website_sessions import _public_transport, website_origin

from .checks import BrowserChecks, confirmed_regressions
from .deployment import Deployments
from .models import (
    ACTIVE,
    Check,
    CodeChange,
    Conflict,
    Mutation,
    Prepare,
    UnknownOutcome,
    WordPressError,
    digest,
    owns_browser,
)
from .state import Store
from .transport import Client, Companion


@dataclass(frozen=True)
class Actor:
    directory: str
    session_id: str
    operator: bool = False


class Service:
    def __init__(
        self,
        runtime_dir,
        actor,
        host_service,
        *,
        authorize=None,
        transport_factory=_public_transport,
        checks=None,
        wait=asyncio.sleep,
    ):
        self.store = Store(runtime_dir)
        self.actor, self.directory, self.host_service = actor, actor.directory, host_service
        self.authorize = authorize
        self.transport_factory = transport_factory
        self.checks = checks or BrowserChecks(self.store)
        self.wait = wait
        self.deployments = Deployments(self)

    async def host_auth(self, connector_id):
        try:
            return await self.host_service.host_auth(self.directory, connector_id)
        except Exception:
            raise WordPressError("Host connector binding is unavailable") from None

    async def headers(self, connector_id):
        return (await self.host_auth(connector_id))[1]

    async def auth_material(self, connector_id):
        resolve = getattr(self.host_service, "host_auth_material", None)
        if callable(resolve):
            try:
                _, headers, secrets = await resolve(self.directory, connector_id)
                return headers, secrets
            except Exception:
                raise WordPressError("Host connector binding is unavailable") from None
        definition, headers = await self.host_auth(connector_id)
        if definition.auth_profile:
            raise WordPressError("Host credential material is unavailable")
        return headers, ()

    def site(self, site_id, *, configure=False):
        _, settings = self.store.settings(self.directory)
        site = settings.sites.get(site_id)
        if site is None or (not configure and not site.enabled):
            raise WordPressError("WordPress site is unavailable in this directory")
        if not configure and site.recovery_verified != site.binding():
            raise WordPressError("Run and verify the restore probe before enabling edits")
        return site

    async def companion(self, site):
        definition, _ = await self.host_auth(site.connector_id)
        if (
            definition.driver != "wordpress"
            or website_origin(definition.config.get("base_url", "")) != site.origin
        ):
            raise WordPressError("The site's WordPress connector binding does not match")
        return Companion(
            Client(
                site.origin,
                lambda: self.headers(site.connector_id),
                auth_material=lambda: self.auth_material(site.connector_id),
                transport_factory=self.transport_factory,
            ),
            site.rest_path,
        )

    @staticmethod
    def connector_ids(site):
        ids = [site.connector_id]
        if site.deployment and site.deployment.kind == "pipeline":
            ids.append(site.deployment.connector_id)
        if site.source and site.source.build_connector_id:
            ids.append(site.source.build_connector_id)
        return sorted(set(ids))

    @classmethod
    def config_binding(cls, site, config):
        definitions = []
        for id in cls.connector_ids(site):
            definition = config.connectors[id]
            profile = config.auth_profiles.get(definition.auth_profile)
            definitions.append(
                [
                    id,
                    definition.model_dump(mode="json"),
                    profile.model_dump(mode="json") if profile else None,
                ]
            )
        return digest([site.binding(), definitions])

    async def binding(self, site):
        for id in self.connector_ids(site):
            await self.host_auth(id)
        config = self.host_service.provider_config.resolve_connectors_for_directory(self.directory)
        return self.config_binding(site, config)

    def reauthorize(self):
        if self.authorize:
            self.authorize()
        elif not self.actor.operator:
            raise WordPressError("Current session policy is required for website mutations")

    @staticmethod
    def receipt(change):
        # Recovery data, cookies and host paths stay private; expose only artifact IDs.
        allowed = {
            "change_id",
            "site_id",
            "revision",
            "state",
            "summary",
            "created_at",
            "updated_at",
            "urls",
            "checks",
            "baseline",
            "screenshots",
            "error",
            "actor",
            "expires_at",
            "browser_ready",
            "commit_sha",
            "release",
            "recovery",
            "ready",
            "phase",
            "resolution",
        }
        value = {key: item for key, item in change.items() if key in allowed}
        for field in ("release", "recovery"):
            if field in value:

                def public(item):
                    return {
                        key: part
                        for key, part in item.items()
                        if key
                        in {
                            "state",
                            "release_id",
                            "release_version",
                            "commit_sha",
                            "artifact_sha256",
                        }
                    }

                value[field] = (
                    public(value[field])
                    if field == "release"
                    else {
                        key: public(part)
                        for key, part in value[field].items()
                        if isinstance(part, dict)
                    }
                )
        value["undo_id"] = change["change_id"]
        value["build_pending"] = bool(change.get("build_pending"))
        return value

    async def discover(self, site_id):
        site = self.site(site_id, configure=True)
        plugin = await self.companion(site)
        return {
            "site_id": site_id,
            "capabilities": sorted(site.capabilities),
            "enabled": site.enabled,
            "recovery_verified": site.recovery_verified == site.binding(),
            **await plugin.discover(),
        }

    async def test(self, site_id):
        site = self.site(site_id, configure=True)
        plugin = await self.companion(site)
        discovered = await plugin.discover()
        if not discovered.get("transactional"):
            raise WordPressError("The companion's resource tables must use InnoDB")
        if "browser" in site.capabilities and not discovered.get("browser_ready"):
            raise WordPressError(
                "Active WordPress plugins have not declared browser recovery coverage"
            )
        from uuid import uuid4

        result = await plugin.call("probe", {"change_id": "wp_" + uuid4().hex})
        if not result.get("restore_verified"):
            raise WordPressError("The CMS restore probe failed")
        if "source" in site.capabilities:
            adapter = await self.deployments.adapter(site_id, site)
            external = await adapter.call(
                "probe",
                {"operation_id": "probe:" + uuid4().hex, "component": site.source.component},
            )
            if not external.get("restore_verified"):
                raise WordPressError("The hosting restore probe failed")
        # A browser probe verifies the installed Playwright runtime and current critical pages.
        await self.checks.run(site, site.checks, "setup")
        revision, settings = self.store.settings(self.directory)
        if settings.sites[site_id].binding() != site.binding():
            raise Conflict("Site configuration changed during the restore test")
        settings.sites[site_id].recovery_verified = site.binding()
        self.store.save_settings(self.directory, revision, settings)
        return {"site_id": site_id, "restore_verified": True, "protocol_version": 1}

    async def inspect(self, site_id, key):
        site = self.site(site_id)
        return await (await self.companion(site)).call("inspect", {"key": key})

    async def prepare(self, request):
        request = Prepare.model_validate(request)
        site = self.site(request.site_id)
        self.reauthorize()
        if request.resources and "cms" not in site.capabilities:
            raise WordPressError("CMS changes are not enabled")
        if request.browser and "browser" not in site.capabilities:
            raise WordPressError("Browser changes are not enabled")
        if request.code and "source" not in site.capabilities:
            raise WordPressError("Source changes are not enabled")
        payload = request.model_dump(mode="json")
        if request.change_id:
            return await self.extend(request)
        with self.store.lease(site.origin) as fence:
            existing = self.store.request(
                self.directory, request.site_id, "prepare_change", request.idempotency_key, payload
            )
            if existing and (existing.get("ready") or existing["state"] != "prepared"):
                return self.receipt(existing)
            self.store.require_clear(site.origin, existing["change_id"] if existing else None)
            checks = list(
                {
                    digest(check.model_dump()): check for check in [*site.checks, *request.checks]
                }.values()
            )
            change = existing or self.store.create(
                self.directory,
                request.site_id,
                site.origin,
                {
                    "summary": request.summary,
                    "actor": self.actor.session_id,
                    "operator": self.actor.operator,
                    "request": payload,
                    "preparation_requests": [payload],
                    "binding": await self.binding(site),
                    "expires_at": time.time() + site.change_ttl_seconds,
                    "urls": [site.origin + check.path for check in checks],
                    "check_specs": [check.model_dump() for check in checks],
                    "ready": False,
                },
                request=payload,
            )
            try:
                plugin = await self.companion(site)
                discovered = await plugin.discover()
                if not discovered.get("transactional") or (
                    request.browser and not discovered.get("browser_ready")
                ):
                    raise WordPressError("WordPress recovery coverage is incomplete")
                baseline = await self.checks.run(site, checks, "before")
                if baseline.get("uncertain"):
                    raise WordPressError("Baseline checks could not reach all requested pages")
                change = self.store.update(
                    change, baseline=baseline, screenshots=baseline.get("screenshots", [])
                )
                if request.resources:
                    captured = await plugin.call(
                        "prepare",
                        {
                            "change_id": change["change_id"],
                            "expires_at": int(change["expires_at"]),
                            "resources": [row.model_dump() for row in request.resources],
                        },
                    )
                    snapshot = captured.get("snapshot")
                    if not isinstance(snapshot, dict) or set(snapshot) != {
                        row.key for row in request.resources
                    }:
                        raise WordPressError(
                            "Companion snapshot does not cover the requested resources"
                        )
                    artifact = self.store.artifact(json.dumps(snapshot).encode())
                    change = self.store.update(
                        change, snapshot=artifact, snapshot_digest=digest(snapshot)
                    )
                if request.code:
                    deployment = await self.deployments.prepare(
                        request.site_id, site, change, request.code
                    )
                    change = self.store.update(
                        change,
                        deployment=None if deployment.get("building") else deployment,
                        build_pending=deployment if deployment.get("building") else None,
                        commit_sha=request.code.commit_sha,
                    )
                self.reauthorize()
                if change["binding"] != await self.binding(self.site(request.site_id)):
                    raise Conflict("Configuration changed while preparing")
                # Browser edits can begin after all recovery artifacts are fsynced.
                if request.browser:
                    await plugin.call(
                        "arm",
                        {
                            "change_id": change["change_id"],
                            "fence": fence,
                            "snapshot_digest": change["snapshot_digest"],
                        },
                        mutation=True,
                    )
                change = self.store.update(change, ready=True, browser_ready=request.browser)
            except Exception:
                change = self.store.update(
                    change, state="failed", error="Preparation failed; no change was published"
                )
                raise
            return self.receipt(change)

    async def extend(self, request):
        site = self.site(request.site_id)
        payload = request.model_dump(mode="json")
        with self.store.lease(site.origin) as fence:
            prior = self.store.request(
                self.directory, request.site_id, "prepare_change", request.idempotency_key, payload
            )
            if prior:
                return self.receipt(prior)
            change = self.store.get(request.change_id, self.directory, request.site_id)
            if (
                change["revision"] != request.expected_revision
                or change["state"] != "prepared"
                or not change.get("ready")
                or change["expires_at"] <= time.time()
            ):
                raise Conflict("Only a current prepared change can be extended")
            if not self.actor.operator and change["actor"] != self.actor.session_id:
                raise Conflict("Change belongs to a different session")
            if change["binding"] != await self.binding(site):
                raise Conflict("Prepared configuration changed")
            preparation_requests = self.store.preparation_requests(change)
            plugin = await self.companion(site)
            captured = await plugin.call(
                "prepare-extend",
                {
                    "change_id": change["change_id"],
                    "idempotency_key": request.idempotency_key,
                    "resources": [row.model_dump() for row in request.resources],
                },
            )
            extra = captured["snapshot"]
            if set(extra) != {row.key for row in request.resources}:
                raise WordPressError("Extension snapshot does not cover the requested resources")
            original = json.loads(self.store.read_artifact(change["snapshot"]))
            if set(original) & set(extra):
                raise Conflict("Resource is already covered by this change")
            snapshot = {**original, **extra}
            artifact = self.store.artifact(json.dumps(snapshot).encode())
            change = self.store.update(
                change,
                snapshot=artifact,
                snapshot_digest=digest(snapshot),
                preparation_requests=[*preparation_requests, payload],
                ready=False,
                browser_ready=False,
                phase="extend",
                extension_pending=request.idempotency_key,
                request=("prepare_change", request.idempotency_key, payload),
            )
            try:
                change = await self._finish_extension(site, change, fence)
            except WordPressError:
                change = self.store.update(
                    change, error="New resource coverage awaits reconciliation"
                )
            return self.receipt(change)

    async def _finish_extension(self, site, change, fence):
        self.reauthorize()
        plugin = await self.companion(site)
        await plugin.call(
            "arm-extend",
            {
                "change_id": change["change_id"],
                "fence": fence,
                "idempotency_key": change["extension_pending"],
                "snapshot_digest": change["snapshot_digest"],
            },
            mutation=True,
        )
        return self.store.update(
            change,
            extension_pending=None,
            ready=True,
            browser_ready=change["request"]["browser"],
            phase=None,
            error=None,
        )

    def status(self, site_id, change_id=None):
        if change_id:
            return self.receipt(self.store.get(change_id, self.directory, site_id))
        return {"changes": [self.receipt(row) for row in self.store.list(self.directory, site_id)]}

    async def queue(self, request, *, restore=False):
        request = Mutation.model_validate(request)
        site = self.site(request.site_id, configure=restore)
        self.reauthorize()
        payload, operation = request.model_dump(), "restore_change" if restore else "apply_change"
        with self.store.lease(site.origin):
            existing = self.store.request(
                self.directory, request.site_id, operation, request.idempotency_key, payload
            )
            if existing:
                return self.receipt(existing)
            change = self.store.get(request.change_id, self.directory, request.site_id)
            if not self.actor.operator and change["actor"] != self.actor.session_id:
                raise Conflict("Change belongs to a different session")
            if change["revision"] != request.expected_revision:
                raise Conflict("Reload the current change revision")
            self.store.require_clear(site.origin, change["change_id"])
            # Pending coverage has already persisted the combined recovery manifest.
            # Undo must remain available even though new editing is not yet ready.
            if not change.get("ready") and not (
                restore and change.get("extension_pending") and change.get("snapshot")
            ):
                raise WordPressError("Recovery preparation is incomplete")
            if restore:
                if change["state"] not in {
                    "prepared",
                    "published",
                    "conflict",
                    "uncertain",
                    "recovery_required",
                    "failed",
                }:
                    raise Conflict("Change cannot be restored in its current state")
            elif change["state"] != "prepared" or change["expires_at"] <= time.time():
                raise Conflict("Change is not prepared or has expired")
            if change["binding"] != await self.binding(site):
                raise Conflict("The prepared configuration or credential binding changed")
            change = self.store.update(
                change,
                state="restoring" if restore else "queued",
                publication_request=change.get("publication_request") if restore else payload,
                last_requested_by=self.actor.session_id,
                request=(operation, request.idempotency_key, payload),
            )
            return self.receipt(change)

    async def browser_state(self, site_id, change_id, session_expiry):
        site = self.site(site_id)
        self.reauthorize()
        with self.store.lease(site.origin) as fence:
            change = self.store.get(change_id, self.directory, site_id)
            if (
                change["actor"] != self.actor.session_id
                or change["state"] != "prepared"
                or not change.get("browser_ready")
                or change["expires_at"] <= time.time()
            ):
                raise Conflict("No active prepared browser change belongs to this session")
            if change["binding"] != await self.binding(site):
                raise Conflict("Browser change configuration changed")
            plugin = await self.companion(site)
            ticket = await plugin.call(
                "browser/ticket",
                {
                    "change_id": change_id,
                    "fence": fence,
                    "expires_at": int(min(session_expiry, change["expires_at"], time.time() + 900)),
                },
            )
            state = await plugin.call("browser/redeem", {"ticket": ticket["ticket"]}, mutation=True)
            self.reauthorize()
            if state.get("origin") != site.origin:
                raise WordPressError("Browser session origin mismatch")
            # The broker returns this temporary state directly; it is never an MCP receipt or ledger field.
            return state

    async def resolve(self, request):
        """Let an operator retain verified human edits and release a conflicted site."""
        if not self.actor.operator:
            raise PermissionError("Only an operator can resolve a quarantined change")
        request = Mutation.model_validate(request)
        site = self.site(request.site_id, configure=True)
        payload = request.model_dump()
        with self.store.lease(site.origin):
            existing = self.store.request(
                self.directory, request.site_id, "resolve_change", request.idempotency_key, payload
            )
            if existing:
                return self.receipt(existing)
            change = self.store.get(request.change_id, self.directory, request.site_id)
            if change["revision"] != request.expected_revision or change["state"] not in {
                "conflict",
                "uncertain",
                "recovery_required",
            }:
                raise Conflict("Reload the unresolved change before keeping the current website")
            self.store.require_clear(site.origin, change["change_id"])
            if change["binding"] != await self.binding(site):
                raise Conflict("Recovery configuration changed")
            if change.get("deployment"):
                status = await (await self.deployments.adapter(request.site_id, site)).call(
                    "status",
                    {"operation_id": change["change_id"], "component": site.source.component},
                )
                if status.get("state") not in {
                    "not_found",
                    "prepared",
                    "deployed",
                    "restored",
                    "conflict",
                }:
                    raise UnknownOutcome(
                        "Reconcile the hosting operation before keeping the current website"
                    )
            if change.get("snapshot"):
                await (await self.companion(site)).call(
                    "close", {"change_id": change["change_id"]}, mutation=True
                )
            change, failures = await self._verify(site, change)
            if failures:
                raise WordPressError(
                    "Current website has confirmed regressions; restore or repair it before resolving"
                )
            return self.receipt(
                self.store.update(
                    change,
                    state="resolved",
                    browser_ready=False,
                    phase=None,
                    error=None,
                    resolution={"action": "keep_current", "actor": self.actor.session_id},
                    request=("resolve_change", request.idempotency_key, payload),
                )
            )

    async def _verify(self, site, change, *, restoring=False):
        specs = [Check.model_validate(row) for row in change["check_specs"]]
        first = await self.checks.run(site, specs, "restored" if restoring else "after")
        await self.wait(10)
        second = await self.checks.run(site, specs, "restored-confirm" if restoring else "confirm")
        failures = confirmed_regressions(change["baseline"], first, second)
        change = self.store.update(
            change,
            checks={"first": first, "second": second, "regressions": failures},
            screenshots=[
                *change.get("screenshots", []),
                *first.get("screenshots", []),
                *second.get("screenshots", []),
            ],
        )
        if first.get("uncertain") or second.get("uncertain"):
            raise UnknownOutcome("Live verification is incomplete")
        return change, failures

    async def _restore(self, site_id, site, change, fence):
        # Code is restored externally first, so a PHP fatal cannot disable CMS recovery.
        if change.get("deployment"):
            release = await self.deployments.restore(site_id, site, change, fence)
            if release.get("state") != "restored":
                raise UnknownOutcome("Hosting restore is still pending")
            change = self.store.update(change, recovery={"release": release})
        plugin = await self.companion(site)
        if change.get("snapshot"):
            snapshot = json.loads(self.store.read_artifact(change["snapshot"]))
            result = await plugin.call(
                "restore",
                {"change_id": change["change_id"], "fence": fence, "snapshot": snapshot},
                mutation=True,
            )
            if result.get("state") != "restored":
                raise UnknownOutcome("CMS restore is still pending")
            change = self.store.update(
                change, recovery={**change.get("recovery", {}), "cms": result}
            )
        change, failures = await self._verify(site, change, restoring=True)
        return self.store.update(
            change,
            state="recovery_required" if failures else "restored",
            browser_ready=False,
            extension_pending=None,
            error="Restored website failed verification" if failures else None,
        )

    def _record_conflict(self, site_id, change_id):
        current = self.store.get(change_id, self.directory, site_id)
        return self.receipt(
            self.store.update(
                current,
                state="recovery_required" if current.get("phase") == "restore" else "conflict",
                error="Concurrent edits or changed configuration require reconciliation",
            )
        )

    async def _recover_expired_cms(self, site_id, site, change, fence):
        if (
            not change.get("snapshot")
            or change["request"]["browser"]
            or int(change["expires_at"]) > time.time()
        ):
            return None
        status = await (await self.companion(site)).call(
            "status", {"change_id": change["change_id"]}
        )
        # An applied request remains idempotent after expiry, including a lost reply.
        if status.get("state") == "applied":
            return None
        change = self.store.update(
            change,
            state="restoring",
            phase="restore",
            error="CMS preparation expired before publication",
        )
        return self.receipt(await self._restore(site_id, site, change, fence))

    async def execute(self, site_id, change_id):
        initial = self.store.get(change_id, self.directory, site_id)
        site = self.site(
            site_id,
            configure=initial["state"] == "restoring"
            or initial.get("phase") == "restore"
            or owns_browser(initial),
        )
        if (
            initial["state"] == "prepared"
            and not initial.get("ready")
            and not initial.get("extension_pending")
        ):
            return await self.prepare(initial["request"])
        with self.store.lease(site.origin) as fence:
            change = self.store.get(change_id, self.directory, site_id)
            if change["state"] in {
                "published",
                "restored",
                "failed",
                "conflict",
                "recovery_required",
                "resolved",
            }:
                return self.receipt(change)
            self.store.require_clear(site.origin, change_id)
            if (
                change["state"] in ACTIVE | {"prepared"}
                and owns_browser(change)
                and change.get("phase") != "restore"
                and change["state"] != "restoring"
            ):
                recovery_reason = None
                if change["expires_at"] <= time.time() or not site.enabled:
                    recovery_reason = "Browser change expired or site was disabled"
                else:
                    try:
                        self.reauthorize()
                    except Exception:
                        recovery_reason = "Browser session authorization was revoked"
                if recovery_reason:
                    change = self.store.update(
                        change, state="restoring", phase="restore", error=recovery_reason
                    )
            if change["state"] == "prepared" and change.get("extension_pending"):
                try:
                    return self.receipt(await self._finish_extension(site, change, fence))
                except WordPressError:
                    return self.receipt(change)
            if change["state"] == "prepared":
                return self.receipt(change)
            try:
                if change["binding"] != await self.binding(site):
                    raise Conflict("Configuration or credential binding changed during execution")
                if change["state"] == "restoring" or change.get("phase") == "restore":
                    change = self.store.update(change, state="restoring", phase="restore")
                    return self.receipt(await self._restore(site_id, site, change, fence))
                self.reauthorize()
                recovered = await self._recover_expired_cms(site_id, site, change, fence)
                if recovered is not None:
                    return recovered
                change = self.store.update(change, state="applying")
                if change["request"]["browser"] and not change.get("browser_closed"):
                    plugin = await self.companion(site)
                    result = await plugin.call("close", {"change_id": change_id}, mutation=True)
                    change = self.store.update(change, cms_result=result, browser_closed=True)
                if change.get("build_pending"):
                    deployment = await self.deployments.prepare(
                        site_id, site, change, CodeChange.model_validate(change["request"]["code"])
                    )
                    if deployment.get("building"):
                        return self.receipt(
                            self.store.update(
                                change, state="queued", phase="build", build_pending=deployment
                            )
                        )
                    change = self.store.update(
                        change, deployment=deployment, build_pending=None, phase="deploy"
                    )
                recovered = await self._recover_expired_cms(site_id, site, change, fence)
                if recovered is not None:
                    return recovered
                if change.get("deployment") and change.get("phase") not in {"cms", "verify"}:
                    change = self.store.update(change, phase="deploy")
                    release = await self.deployments.apply(site_id, site, change, fence)
                    if release.get("state") != "deployed":
                        raise UnknownOutcome("Deployment is not confirmed")
                    change = self.store.update(change, release=release, phase="cms")
                recovered = await self._recover_expired_cms(site_id, site, change, fence)
                if recovered is not None:
                    return recovered
                self.reauthorize()
                plugin = await self.companion(site)
                if change.get("snapshot") and change.get("phase") != "verify":
                    change = self.store.update(change, phase="cms")
                    if change["request"]["browser"]:
                        result = change["cms_result"]
                    else:
                        status = await plugin.call("status", {"change_id": change_id})
                        if status["state"] == "prepared":
                            await plugin.call(
                                "arm",
                                {
                                    "change_id": change_id,
                                    "fence": fence,
                                    "snapshot_digest": change["snapshot_digest"],
                                },
                                mutation=True,
                            )
                        result = await plugin.call(
                            "apply", {"change_id": change_id, "fence": fence}, mutation=True
                        )
                    if result.get("state") != "applied":
                        raise UnknownOutcome("CMS publication is not confirmed")
                    change = self.store.update(change, cms_result=result)
                change = self.store.update(change, state="verifying", phase="verify")
                change, failures = await self._verify(site, change)
                if failures:
                    change = self.store.update(
                        change,
                        state="restoring",
                        phase="restore",
                        error="Confirmed functional regression",
                    )
                    return self.receipt(await self._restore(site_id, site, change, fence))
                return self.receipt(
                    self.store.update(change, state="published", browser_ready=False, error=None)
                )
            except Conflict:
                current = self.store.get(change_id, self.directory, site_id)
                try:
                    recovered = await self._recover_expired_cms(site_id, site, current, fence)
                    if recovered is not None:
                        return recovered
                except Conflict:
                    return self._record_conflict(site_id, change_id)
                except Exception:
                    current = self.store.get(change_id, self.directory, site_id)
                    return self.receipt(
                        self.store.update(
                            current,
                            state="uncertain",
                            error="Expired publication needs recovery reconciliation",
                        )
                    )
                return self._record_conflict(site_id, change_id)
            except (UnknownOutcome, WordPressError):
                current = self.store.get(change_id, self.directory, site_id)
                # A broken PHP deployment can prevent REST status reconciliation. Verify
                # externally and recover it when two probes confirm a new objective failure.
                if current.get("deployment") and current.get("phase") != "restore":
                    try:
                        current, failures = await self._verify(site, current)
                        if failures:
                            current = self.store.update(current, state="restoring", phase="restore")
                            return self.receipt(await self._restore(site_id, site, current, fence))
                    except Conflict:
                        return self._record_conflict(site_id, change_id)
                    except Exception:
                        current = self.store.get(change_id, self.directory, site_id)
                return self.receipt(
                    self.store.update(
                        current,
                        state="uncertain",
                        error="Remote outcome is uncertain; status reconciliation is required",
                    )
                )
            except Exception:
                current = self.store.get(change_id, self.directory, site_id)
                return self.receipt(
                    self.store.update(
                        current,
                        state="uncertain",
                        error="Execution needs reconciliation; existing recovery data has been retained",
                    )
                )
