"""Browser-only worker. Applications are leased HTTP endpoints owned by other add-ons."""

import ipaddress
import json
import os
import secrets
import shutil
import socket
import threading
from urllib.parse import urlsplit
import httpx
from pydantic import Field, field_validator
from evander.resources.container_runtime import ContainerSettings, ContainerSupervisor, RuntimeError
from evander.resources.endpoints import Endpoints
from evander.resources.jobs import Jobs
from evander.resources.models import Lease, Owner, Profile
from .models import BrowserError, Session, LaunchProfile, origin

CONTROL_PORT = 11895


class WorkerSettings(ContainerSettings):
    token_env: str = "EVANDER_BROWSER_WORKER_TOKEN"
    callback_url: str = "http://127.0.0.1:11891"
    profiles: dict[str, LaunchProfile] = Field(default_factory=dict)
    relay_urls: list[str] = Field(default_factory=list)
    http_target_browser_proxy_url: str | None = None
    http_target_relay_url: str | None = None

    @field_validator("http_target_browser_proxy_url", "http_target_relay_url")
    @classmethod
    def proxy_origin(cls, value):
        if value is not None and origin(value) != value.rstrip("/"):
            raise ValueError("Expected an HTTP origin")
        return value.rstrip("/") if value else value


class Supervisor(ContainerSupervisor):
    namespace = "browser-sessions"

    def __init__(self, settings, *, jobs=None, **kwargs):
        super().__init__(settings, **kwargs)
        self.endpoints = Endpoints(settings.worker_root / "runtime")
        self.jobs = jobs or Jobs(
            "browser-sessions-addon", settings.worker_root / "runtime/worker-jobs.db"
        )

    def targets(self, session):
        endpoints = []
        for ref in session.endpoint_refs:
            try:
                endpoints.append(
                    self.endpoints.resolve(
                        ref,
                        session_id=session.agent_session_id,
                        directory=session.directory,
                        worker_id=self.settings.worker_id,
                    )
                )
            except ValueError:
                if session.endpoint_origins.get(ref) in session.allowed_origins:
                    raise
        return endpoints

    def create(self, payload):
        session = Session.model_validate(payload["session"])
        profile = LaunchProfile.model_validate(payload["profile"])
        if session.mode != "hosted" or session.worker_id != self.settings.worker_id:
            raise BrowserError("Browser worker assignment mismatch")
        if not self.settings.allocations_enabled:
            raise BrowserError("Browser allocations are disabled")
        if self.settings.profiles.get(session.profile_id) != profile:
            raise BrowserError("Browser profile differs from worker approval")
        if not self.now() < session.expires_at <= self.now() + 86400:
            raise BrowserError("Invalid browser lease")
        if payload["relay_url"] not in self.settings.relay_urls:
            raise BrowserError("Browser relay is not approved")
        if session.target_aliases and not self.settings.http_target_browser_proxy_url:
            raise BrowserError("HTTP target browser proxy is not configured")
        self.targets(session)
        if origin(session.entry_url) not in session.allowed_origins:
            raise BrowserError("Browser entry origin is not approved")
        with self.lock:
            try:
                old = self.get(session.id)
            except RuntimeError as exc:
                if exc.status != 404:
                    raise
                old = None
            if old:
                if old["session"] != session.model_dump() or old["profile"] != profile.model_dump():
                    raise BrowserError("Browser identity conflict")
                return self.receipt(old)
            active = [lease for lease in self.leases() if lease["state"] != "closed"]
            if len(active) >= self.settings.max_sessions or any(
                lease["session"]["agent_session_id"] == session.agent_session_id for lease in active
            ):
                raise BrowserError("Browser quota reached")
            lease = {
                "id": session.id,
                "name": "evbs-" + session.id.removeprefix("browser-"),
                "state": "accepted",
                "session": session.model_dump(),
                "profile": profile.model_dump(),
                "container_user": self.settings.container_user,
                "expires_at": session.expires_at,
                "relay_url": payload["relay_url"],
                "endpoint_token": payload["endpoint_token"],
                "proxy_token": payload["proxy_token"],
                "allowed_origins": session.allowed_origins,
                "error": None,
                "target_bindings": [],
                "binding": None,
            }
            self.dependencies.register(
                session.agent_session_id,
                session.id + ":worker",
                self.settings.callback_url + "/v1/sessions/" + session.id + "/close",
                self.settings.token_env,
            )
            self.save(lease)
            return self.receipt(lease)

    def receipt(self, lease):
        return {
            "id": lease["id"],
            "state": lease["state"],
            "allowed_origins": lease["allowed_origins"],
            "control_generation": lease["session"]["control_generation"],
            "error": lease["error"],
        }

    def controller(self, lease, payload):
        try:
            with httpx.Client(timeout=15, trust_env=False) as client:
                response = client.post(
                    f"http://{lease['browser_ip']}:{CONTROL_PORT}/control",
                    json=payload,
                    headers={"Authorization": "Bearer " + lease["endpoint_token"]},
                )
                response.raise_for_status()
                value = response.json()
                if value.get("fenced") is not True:
                    raise ValueError()
                return value
        except (httpx.HTTPError, ValueError):
            raise BrowserError(
                "Browser control fence is unconfirmed", "fence_unconfirmed", 503
            ) from None

    def fence(self, session_id, payload):
        with self.lock:
            lease = self.get(session_id)
            session = Session.model_validate(payload["session"])
            if (
                session.id != session_id
                or session.agent_session_id != lease["session"]["agent_session_id"]
                or session.control_generation < lease["session"]["control_generation"]
            ):
                raise BrowserError("Invalid browser control generation")
            result = self.controller(lease, {"session": session.model_dump()})
            lease["session"] = session.model_dump()
            self.save(lease)
            return result

    def connect(self, session_id, payload):
        with self.lock:
            lease = self.get(session_id)
            session = Session.model_validate(payload["session"])
            profile = LaunchProfile.model_validate(lease["profile"])
            if (
                not profile.playwright_enabled
                or session.id != session_id
                or session.agent_session_id != lease["session"]["agent_session_id"]
                or session.controller != "agent:" + session.agent_session_id
                or session.state != "ready"
            ):
                raise BrowserError("Playwright control is unavailable", "control_required", 403)
            self.targets(session)
            owner = Owner(
                key=session.agent_session_id,
                session_id=session.agent_session_id,
                directory=session.directory,
                valid_until=session.expires_at,
            )
            job = self.jobs.current(owner)
            if job is None:
                raise BrowserError("Playwright requires the owner's current worker job")
            if lease.get("binding"):
                binding = Lease.model_validate(lease["binding"])
                self.jobs.unbind(binding, lambda: self._save_binding(lease, binding))
            token = secrets.token_urlsafe(32)
            binding = Lease(
                lease_id="rs-" + secrets.token_hex(16),
                owner=owner,
                profile_id=session.profile_id,
                profile=Profile(),
                created_at=self.now(),
                expires_at=session.expires_at,
                resources={"network": lease["name"]},
            )
            self._save_binding(lease, binding)
            self.controller(lease, {"session": session.model_dump(), "automation_token": token})
            connection = {
                "browser": {
                    "host": lease["browser_ip"],
                    "port": CONTROL_PORT,
                    "endpoint": f"ws://{lease['browser_ip']}:{CONTROL_PORT}/connect",
                    "headers": {
                        "Authorization": "Bearer " + token,
                        "X-Evander-Control-Generation": str(session.control_generation),
                    },
                    "session_id": session.id,
                    "generation": session.control_generation,
                    "proxy": (
                        {
                            "server": self.settings.http_target_browser_proxy_url,
                            "username": "proxy~" + session.id,
                            "password": lease["proxy_token"],
                            "bypass": ",".join(
                                urlsplit(item.origin).hostname for item in self.targets(session)
                            ),
                        }
                        if session.target_aliases
                        else None
                    ),
                }
            }
            try:
                self.jobs.bind(binding, job, connection, lambda: self._save_binding(lease, binding))
            except Exception:
                self.controller(lease, {"revoke_automation": True})
                raise
            lease["session"] = session.model_dump()
            self.save(lease)
            return {"config_path": binding.config_path}

    def _save_binding(self, lease, binding):
        lease["binding"] = binding.model_dump()
        self.save(lease)

    def detach_resource(self, session_id, resource_id):
        with self.lock:
            lease = self.get(session_id)
            matching = [
                item for item in lease["target_bindings"] if item["resource_id"] == resource_id
            ]
            if not matching:
                return {"released": True}
            revoked = {item["origin"] for item in matching}
            session = Session.model_validate(lease["session"])
            session.allowed_origins = [url for url in session.allowed_origins if url not in revoked]
            session.controller = None
            session.control_generation += 1
            session.state = "paused"
            self.controller(lease, {"session": session.model_dump()})
            for item in matching:
                self._withdraw_target(lease, item)
                lease["target_bindings"].remove(item)
            lease["allowed_origins"] = session.allowed_origins
            lease["session"] = session.model_dump()
            self.save(lease)
            return {"released": True}

    def _withdraw_target(self, lease, item):
        from evander.resources.projection import RESOURCE_CHAIN

        for rule in item.get("rules", []):
            status = self.ipt("-C", RESOURCE_CHAIN, *rule, check=False).returncode
            if status not in {0, 1}:
                raise BrowserError("Cannot verify endpoint network withdrawal")
            if status == 0:
                self.ipt("-D", RESOURCE_CHAIN, *rule)
        # Docker network deletion remains guarded until this consumer detaches.
        value = self.inspect(lease["name"] + "-browser")
        if value and item["network"] in value.get("NetworkSettings", {}).get("Networks", {}):
            self.docker("network", "disconnect", item["network"], lease["name"] + "-browser")
        self.dependencies.remove(lease["id"] + ":target:" + item["ref"])

    def start(self, lease):
        with self.lock:
            operation = self.operations.setdefault(lease["id"], threading.RLock())
        with operation:
            self._start(lease)

    def _start(self, lease):
        with self.lock:
            lease = self.get(lease["id"])
            if lease["state"] != "accepted":
                return
            lease["state"] = "starting"
            if not self.checkpoint(lease):
                return
        try:
            session = Session.model_validate(lease["session"])
            profile = LaunchProfile.model_validate(lease["profile"])
            endpoints = self.targets(session)
            folder = self.root / lease["id"]
            folder.mkdir(mode=0o700, exist_ok=True)
            self.docker(
                "network",
                "create",
                "--label",
                "evander.resource=" + lease["id"],
                "--opt",
                "com.docker.network.bridge.enable_icc=false",
                "--ipv6=false",
                lease["name"],
            )
            subnet = ipaddress.IPv4Network(
                self.inspect(lease["name"], "network")["IPAM"]["Config"][0]["Subnet"]
            )
            lease.update(subnet=str(subnet), browser_ip=str(subnet.network_address + 2), egress=[])
            hosts = []
            for url in [
                lease["relay_url"],
                self.settings.http_target_browser_proxy_url,
                self.settings.http_target_relay_url,
            ]:
                if not url:
                    continue
                parsed = urlsplit(url)
                address = socket.gethostbyname(parsed.hostname)
                hosts += ["--add-host", parsed.hostname + ":" + address]
                lease["egress"].append(
                    (
                        lease["browser_ip"],
                        address,
                        parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80),
                    )
                )
            # Worker-only authenticated control, plus job-specific rules installed by Jobs.bind.
            lease["egress"].append(
                (str(subnet.network_address + 1), lease["browser_ip"], CONTROL_PORT)
            )
            if not self.checkpoint(lease):
                return
            self.firewall(lease)
            config = {
                "session": lease["session"],
                "allowed_origins": lease["allowed_origins"],
                "relay_url": lease["relay_url"],
                "endpoint_token": lease["endpoint_token"],
                "entry_url": session.entry_url,
                "playwright_enabled": profile.playwright_enabled,
                "proxy": (
                    {
                        "server": self.settings.http_target_browser_proxy_url,
                        "username": "proxy~" + session.id,
                        "password": lease["proxy_token"],
                        "bypass": ",".join(urlsplit(item.origin).hostname for item in endpoints),
                    }
                    if session.target_aliases
                    else None
                ),
                "website_session": {
                    "relay_url": self.settings.http_target_relay_url,
                    "session_id": "proxy~" + session.id,
                    "token": lease["proxy_token"],
                    "aliases": session.target_aliases,
                },
            }
            configfile = folder / "browser.json"
            configfile.write_text(json.dumps(config))
            configfile.chmod(0o600)
            os.chown(configfile, *map(int, lease["container_user"].split(":")))
            mounts = []
            from evander.worker.sandbox_profile import (
                WorkerReadOnlyMount,
                validate_worker_read_only_mount,
            )

            for source, target in profile.readonly_mounts.items():
                validate_worker_read_only_mount(
                    WorkerReadOnlyMount(host_path=source, container_path=target)
                )
                if target == "/run" or target.startswith("/run/"):
                    raise BrowserError("Cache mount overlaps browser state")
                mounts += ["--mount", f"type=bind,src={source},dst={target},readonly"]
            startup = {item.startup_state_path for item in endpoints if item.startup_state_path}
            if len(startup) == 1:
                mounts += [
                    "--mount",
                    f"type=bind,src={next(iter(startup))},dst=/run/preview-output,readonly",
                ]
            self.docker(
                "create",
                "--name",
                lease["name"] + "-browser",
                "--label",
                "evander.resource=" + lease["id"],
                "--network",
                lease["name"],
                "--ip",
                lease["browser_ip"],
                "--user",
                lease["container_user"],
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "512",
                "--dns",
                "127.0.0.1",
                "--init",
                "--pull",
                "never",
                "--memory",
                str(profile.memory_mib) + "m",
                "--memory-swap",
                str(profile.memory_mib) + "m",
                "--cpus",
                str(profile.cpus),
                "--env",
                "EVANDER_BROWSER_CONNECTION=/run/browser.json",
                "--mount",
                f"type=bind,src={configfile},dst=/run/browser.json,readonly",
                *hosts,
                *sum(
                    (
                        ["--add-host", urlsplit(item.origin).hostname + ":" + item.address]
                        for item in endpoints
                    ),
                    [],
                ),
                *mounts,
                profile.image,
            )
            from evander.resources.projection import RESOURCE_CHAIN

            networks = {}
            for endpoint in endpoints:
                if endpoint.network not in networks:
                    network = self.inspect(endpoint.network, "network")
                    address = str(
                        ipaddress.IPv4Network(
                            network["IPAM"]["Config"][0]["Subnet"]
                        ).network_address
                        + 3
                    )
                    self.docker(
                        "network",
                        "connect",
                        "--ip",
                        address,
                        endpoint.network,
                        lease["name"] + "-browser",
                    )
                    networks[endpoint.network] = address
                rule = [
                    "-s",
                    networks[endpoint.network] + "/32",
                    "-d",
                    endpoint.address + "/32",
                    "-p",
                    "tcp",
                    "--dport",
                    str(endpoint.port),
                    "-m",
                    "comment",
                    "--comment",
                    lease["id"] + ":" + endpoint.ref,
                    "-j",
                    "ACCEPT",
                ]
                rules = [rule]
                # Docker may choose an attached application's bridge as the default
                # route. Preserve only the same explicitly approved host services
                # from that interface; its subnet remains otherwise denied.
                for source, destination, port in lease["egress"]:
                    if source != lease["browser_ip"]:
                        continue
                    rules.append(
                        [
                            "-s",
                            networks[endpoint.network] + "/32",
                            "-d",
                            destination + "/32",
                            "-p",
                            "tcp",
                            "--dport",
                            str(port),
                            "-m",
                            "comment",
                            "--comment",
                            lease["id"] + ":" + endpoint.ref,
                            "-j",
                            "ACCEPT",
                        ]
                    )
                item = {**endpoint.model_dump(), "rules": rules}
                lease["target_bindings"].append(item)
                if not self.checkpoint(lease):
                    return
                self.dependencies.register(
                    "resource:" + endpoint.resource_id,
                    lease["id"] + ":target:" + endpoint.ref,
                    self.settings.callback_url
                    + "/v1/sessions/"
                    + lease["id"]
                    + "/detach-"
                    + endpoint.resource_id,
                    self.settings.token_env,
                )
                for allowed in rules:
                    self.jobs.allow_rule(RESOURCE_CHAIN, allowed)
            self.docker("start", lease["name"] + "-browser")
            lease["state"] = "running"
            if not self.checkpoint(lease):
                return
        except Exception as error:
            lease["diagnostic"] = self.diagnostic(error)
            lease["state"], lease["error"] = (
                "failed",
                "Browser startup failed; close to release resources",
            )
            if not self.checkpoint(lease):
                return

    def close(self, session_id):
        with self.lock:
            operation = self.operations.setdefault(session_id, threading.RLock())
            try:
                lease = self.get(session_id)
            except RuntimeError as exc:
                if exc.status == 404:
                    return {"released": True}
                raise
            if lease["state"] == "closed":
                return {"released": True}
            lease["state"] = "closing"
            self.save(lease)
        with operation:
            lease = self.get(session_id)
            if lease["state"] == "closed":
                return {"released": True}
            lease["state"] = "closing"
            self.save(lease)
            try:
                if lease.get("binding"):
                    binding = Lease.model_validate(lease["binding"])
                    self.jobs.unbind(binding, lambda: self._save_binding(lease, binding))
                for item in list(lease["target_bindings"]):
                    self._withdraw_target(lease, item)
                    lease["target_bindings"].remove(item)
                    self.save(lease)
                container = self.inspect(lease["name"] + "-browser")
                if container:
                    if container["Config"].get("Labels", {}).get("evander.resource") != session_id:
                        raise BrowserError("Refusing to remove an unowned browser")
                    self.docker("rm", "--force", lease["name"] + "-browser")
                network = self.inspect(lease["name"], "network")
                if network:
                    if network.get("Labels", {}).get("evander.resource") != session_id:
                        raise BrowserError("Refusing to remove an unowned network")
                    self.docker("network", "rm", lease["name"])
                self.remove_firewall(lease)
                folder = self.root / session_id
                if folder.exists():
                    shutil.rmtree(folder)
                self.dependencies.remove(session_id + ":worker")
                lease["state"], lease["error"] = "closed", None
                lease.pop("endpoint_token", None)
                lease.pop("proxy_token", None)
                self.save(lease)
            except Exception:
                lease["state"], lease["error"] = (
                    "cleanup_failed",
                    "Browser cleanup is unconfirmed; dependency retained",
                )
                self.save(lease)
                raise BrowserError(lease["error"], "cleanup_failed", 503) from None
            return {"released": True}

    def reconcile(self, *, startup=False):
        for lease in self.leases():
            if lease["state"] == "closed":
                continue
            if self.now() >= lease["expires_at"] or lease["state"] in {"closing", "cleanup_failed"}:
                self.close(lease["id"])
            elif startup and lease["state"] == "starting":
                lease["state"], lease["error"] = (
                    "failed",
                    "Startup interrupted; close before retrying",
                )
                self.save(lease)
            elif lease["state"] == "accepted":
                self.start(lease)
            elif lease["state"] == "running":
                if lease.get("binding"):
                    binding = Lease.model_validate(lease["binding"])
                    if self.jobs.current(binding.owner) is None:
                        self.controller(lease, {"revoke_automation": True})
                        self.jobs.unbind(binding, lambda: self._save_binding(lease, binding))
                try:
                    self.targets(Session.model_validate(lease["session"]))
                except ValueError:
                    for item in list(lease["target_bindings"]):
                        try:
                            self.endpoints.resolve(
                                item["ref"],
                                session_id=lease["session"]["agent_session_id"],
                                directory=lease["session"]["directory"],
                            )
                        except ValueError:
                            self.detach_resource(lease["id"], item["resource_id"])
                container = self.inspect(lease["name"] + "-browser")
                if not container or not container.get("State", {}).get("Running"):
                    lease["state"], lease["error"] = (
                        "failed",
                        "Browser exited; close to release resources",
                    )
                    self.save(lease)
