"""Single-use plugin tickets, mediated by the existing managed browser broker."""

from evander.connectors.service import ConnectorService, connector_registry_from_addons
from evander.runtime import build_runtime
from evander.website_sessions import WebsiteSessionError, website_origin
import time

from .service import Actor, Service
from .state import Store


class WordPressBrowserAdapter:
    def __init__(self, resolver):
        self.resolver = resolver
        self.runtime = build_runtime(resolver.config)
        self.host = ConnectorService(
            runtime_dir=resolver.config.runtime_dir,
            provider_config=resolver,
            artifact_store_factory=lambda: None,
            registry=connector_registry_from_addons(
                resolver.config.addon_loader, base_dir=resolver.config.runtime_dir.parent
            ),
            session_authorizer=lambda sid: self.runtime.policy.require_tool(sid, "call_connector"),
        )

    def validate_profile(self, directory, profile):
        try:
            if set(profile.adapter_config) != {"site_id"}:
                raise ValueError
            site = (
                Store(self.resolver.config.runtime_dir)
                .settings(directory)[1]
                .sites[profile.adapter_config["site_id"]]
            )
            if (
                "browser" not in site.capabilities
                or profile.browser_origin != site.origin
                or profile.login_url != site.origin + site.rest_path + "evander/v1/browser/ticket"
            ):
                raise ValueError
        except Exception:
            raise WebsiteSessionError("wordpress_browser_profile_mismatch", 403) from None

    def grant_context(self, session, profile):
        try:
            self.runtime.policy.require_tool(session.session_id, "call_connector")
            self.validate_profile(session.directory_path, profile)
            site_id = profile.adapter_config["site_id"]
            if set(profile.adapter_config) != {"site_id"}:
                raise ValueError
            store = Store(self.resolver.config.runtime_dir)
            site = store.settings(session.directory_path)[1].sites[site_id]
            if (
                not site.enabled
                or "browser" not in site.capabilities
                or site.origin != profile.browser_origin
                or site.recovery_verified != site.binding()
            ):
                raise ValueError
            granted = session.connector_grants.get(site.connector_id, [])
            if "prepare_change" not in granted or "apply_change" not in granted:
                raise ValueError
            target = session.http_target(site.browser_target)
            if (
                target.managed_website_session is None
                or website_origin(target.base_url) != site.origin
            ):
                raise ValueError
            candidates = [
                row
                for row in store.list(session.directory_path, site_id)
                if row["state"] == "prepared"
                and row.get("browser_ready")
                and row["actor"] == session.session_id
                and row["expires_at"] > time.time()
            ]
            if len(candidates) != 1:
                raise ValueError
            change = candidates[0]
            config, _, _ = self.host.require_operation(
                session, site.connector_id, "prepare_change", change["request"]
            )
            self.host.require_operation(
                session,
                site.connector_id,
                "apply_change",
                {
                    "site_id": site_id,
                    "change_id": change["change_id"],
                    "expected_revision": change["revision"],
                    "idempotency_key": "browser-access",
                },
            )
            revisions = []
            for id in Service.connector_ids(site):
                connector = config.connectors[id]
                if (
                    connector.disabled
                    or not self.host.auth_status(
                        config, connector, directory_path=session.directory_path
                    )["available"]
                ):
                    raise ValueError
                profile_binding = config.auth_profiles.get(connector.auth_profile)
                if profile_binding and profile_binding.credential_ref:
                    revisions.append(
                        [
                            id,
                            self.host.credentials.metadata(profile_binding.credential_ref)[
                                "revision"
                            ],
                        ]
                    )
            if Service.config_binding(site, config) != change["binding"]:
                raise ValueError
            return {
                "site_id": site_id,
                "change_id": candidates[0]["change_id"],
                "binding": candidates[0]["binding"],
                "site_binding": site.binding(),
                "credential_revisions": revisions,
            }
        except Exception:
            raise WebsiteSessionError("wordpress_prepared_change_required", 403) from None

    async def login(self, sid, profile, grant, session_expiry):
        def authorize():
            session = self.resolver.get_session_manifest(sid)
            if self.grant_context(session, profile) != grant:
                raise WebsiteSessionError("wordpress_change_revoked", 403)
            self.host.authorize_current(
                sid,
                Store(self.resolver.config.runtime_dir)
                .settings(session.directory_path)[1]
                .sites[grant["site_id"]]
                .connector_id,
                "apply_change",
                {
                    "site_id": grant["site_id"],
                    "change_id": grant["change_id"],
                    "expected_revision": 1,
                    "idempotency_key": "browser-access",
                },
            )

        session = self.resolver.get_session_manifest(sid)
        service = Service(
            self.resolver.config.runtime_dir,
            Actor(session.directory_path, sid),
            self.host,
            authorize=authorize,
        )
        try:
            return await service.browser_state(grant["site_id"], grant["change_id"], session_expiry)
        except Exception:
            raise WebsiteSessionError("wordpress_browser_login_failed") from None
