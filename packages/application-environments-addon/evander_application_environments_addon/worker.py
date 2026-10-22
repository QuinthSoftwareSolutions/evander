"""Private application supervisor. Browser and SQL implementations are optional peers."""

import os
from pathlib import Path
import secrets
import shutil
import sys
import time
import threading
import ipaddress
import httpx
from pydantic import Field
from evander.resources.container_runtime import (
    ContainerSupervisor,
    ContainerSettings,
    RuntimeError,
    contained,
    bounded_json,
    relative_path,
)
from evander.resources.models import Owner, Lease
from evander.resources.endpoints import Endpoint, Endpoints
from evander.resources.workloads import Workload, Workloads
from evander.resources.client import WorkerClient
from evander.resources.projection import PRIVATE_TMPFS_SPEC
from .models import Environment, Profile, LaunchManifest, ResourceBinding


class WorkerSettings(ContainerSettings):
    token_env: str = "EVANDER_APPLICATION_WORKER_TOKEN"
    callback_url: str = "http://127.0.0.1:11893"
    profiles: dict[str, Profile] = Field(default_factory=dict)
    resource_profiles: dict[str, ResourceBinding] = Field(default_factory=dict)


class Supervisor(ContainerSupervisor):
    namespace = "application-environments"

    def __init__(self, settings, **kwargs):
        super().__init__(settings, **kwargs)
        self.endpoints = Endpoints(settings.worker_root / "runtime")
        self.workloads = Workloads(settings.worker_root / "runtime/managed-workloads.db")

    def owner(self, lease):
        session = lease["session"]
        return Owner(
            provider="application-environments-addon",
            key=lease["id"],
            reference={"workload_id": lease["id"]},
            session_id=session["agent_session_id"],
            directory=session["directory"],
            valid_until=lease["expires_at"],
        )

    def ensure_resource(self, lease, release=False):
        resource = Lease.model_validate(lease["resource"])
        if release:
            resource.operation = {
                "id": "release:" + lease["id"],
                "kind": "release",
                "arguments": {},
            }
        client = WorkerClient(resource.worker)
        return client.call(
            resource.lease_id,
            "ensure",
            {"addon_id": "sqlserver-addon", "lease": resource.model_dump()},
        )

    def create(self, payload):
        session = Environment.model_validate(payload["session"])
        profile = Profile.model_validate(payload["profile"])
        if not self.settings.allocations_enabled:
            raise RuntimeError("Worker preview allocations are disabled", "disabled", 409)
        if session.worker_id != self.settings.worker_id:
            raise RuntimeError("Worker assignment mismatch", "worker_mismatch", 409)
        approved = self.settings.profiles.get(session.profile_id)
        if approved is None or approved != profile:
            raise RuntimeError(
                "Launch profile differs from the worker-approved profile", "profile_mismatch", 409
            )
        if not self.now() < session.expires_at <= self.now() + 14400:
            raise RuntimeError("Invalid preview lease")
        with self.lock:
            try:
                old = self.get(session.id)
            except RuntimeError as exc:
                if exc.status != 404:
                    raise
                old = None
            if old:
                if old["session"] != session.model_dump() or old["profile"] != profile.model_dump():
                    raise RuntimeError("Preview identity conflict", "identity_conflict", 409)
                return self.receipt(old)
            active = [item for item in self.leases() if item["state"] != "closed"]
            if len(active) >= self.settings.max_sessions or any(
                item["session"]["agent_session_id"] == session.agent_session_id for item in active
            ):
                raise RuntimeError("Worker preview quota reached", "quota_exceeded", 409)
            workspace = self.resolve_workspace(session.agent_session_id, session.workspace_ref)
            manifest_path = contained(
                workspace, workspace / relative_path(session.launch_manifest_path)
            )
            manifest = LaunchManifest.model_validate(bounded_json(manifest_path))
            if manifest.database and (
                not profile.database_profile
                or profile.database_profile not in self.settings.resource_profiles
            ):
                raise RuntimeError("The application profile has no approved database binding")
            if manifest.database:
                binding = self.settings.resource_profiles[profile.database_profile]
                if (
                    binding.profile.worker_id != self.settings.worker_id
                    or "application-environments-addon"
                    not in binding.profile.allowed_owner_providers
                    or manifest.database not in binding.profile.options.get("databases", ["app"])
                ):
                    raise RuntimeError("Database alias or workload ownership is not approved")
            contained(
                workspace, workspace / manifest.working_directory
            ) if manifest.working_directory != "." else workspace
            name = "evapp-" + session.id.removeprefix("environment-")
            origins = [
                f"http://{name}-app:{port}" for port in [manifest.port, *manifest.additional_ports]
            ]
            lease = {
                "id": session.id,
                "name": name,
                "state": "accepted",
                "session": session.model_dump(),
                "profile": profile.model_dump(),
                "container_user": self.settings.container_user,
                "workspace": str(workspace),
                "manifest": manifest.model_dump(),
                "allowed_origins": origins,
                "expires_at": session.expires_at,
                "error": None,
            }
            lease["workspace_path"] = (
                "/home/agent/"
                + workspace.relative_to(
                    self.session_root(session.agent_session_id) / "agent_home"
                ).as_posix()
            )
            # Durable cleanup registrations precede every resource side effect.
            callback = (
                self.settings.callback_url.rstrip("/") + "/v1/environments/" + session.id + "/close"
            )
            self.dependencies.register(
                session.agent_session_id, session.id + ":worker", callback, self.settings.token_env
            )
            self.dependencies.register(
                "workspace:" + session.agent_session_id + ":" + lease["workspace_path"],
                session.id + ":workspace",
                callback,
                self.settings.token_env,
            )
            if manifest.database:
                lease["resource_binding"] = self.settings.resource_profiles[
                    profile.database_profile
                ].model_dump()
            lease["endpoints"] = [
                Endpoint(
                    ref="ep-" + secrets.token_hex(16),
                    resource_id=session.id,
                    session_id=session.agent_session_id,
                    directory=session.directory,
                    worker_id=session.worker_id,
                    origin=value,
                    expires_at=session.expires_at,
                    port=port,
                ).model_dump()
                for value, port in zip(origins, [manifest.port, *manifest.additional_ports])
            ]
            self.save(lease)
            return self.receipt(lease)

    def receipt(self, lease):
        return {
            "id": lease["id"],
            "state": lease["state"],
            "expires_at": lease["expires_at"],
            "error": lease["error"],
            "workspace_path": lease["workspace_path"],
            "endpoints": [
                {key: item[key] for key in ("ref", "origin", "port")}
                for item in lease.get("endpoints", [])
            ],
        }

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
            profile, manifest = (
                Profile.model_validate(lease["profile"]),
                LaunchManifest.model_validate(lease["manifest"]),
            )
            folder = self.root / lease["id"]
            folder.mkdir(mode=0o700, exist_ok=True)
            output = folder / "output"
            output.mkdir(mode=0o700, exist_ok=True)
            identity = lease.get("container_user", "1000:1000")
            os.chown(output, *map(int, identity.split(":")))
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
            network = self.inspect(lease["name"], "network")
            subnet = ipaddress.IPv4Network(network["IPAM"]["Config"][0]["Subnet"])
            lease.update(
                subnet=str(subnet),
                app_ip=str(subnet.network_address + 2),
                browser_ip=str(subnet.network_address + 3),
            )
            egress = []
            for target in profile.egress_allowlist:
                address, port = target.rsplit(":", 1)
                address, port = str(ipaddress.IPv4Address(address)), int(port)
                if not 1 <= port <= 65535:
                    raise RuntimeError("Invalid approved egress port")
                egress.append((lease["app_ip"], address, port))
            lease["egress"] = egress
            if not self.checkpoint(lease):
                return
            self.firewall(lease)
            environment = {
                **profile.public_environment,
                "EVANDER_PREVIEW_ORIGIN": lease["allowed_origins"][0],
                "EVANDER_PREVIEW_ENTRY_URL": lease["allowed_origins"][0] + manifest.entry_path,
                "EVANDER_PREVIEW_OUTPUT": "/run/preview-output",
                "EVANDER_PREVIEW_SESSION_ID": lease["id"],
            }
            if manifest.database:
                binding = ResourceBinding.model_validate(lease["resource_binding"])
                if "application-environments-addon" not in binding.profile.allowed_owner_providers:
                    raise RuntimeError("SQL profile does not permit application workloads")
                owner = self.owner(lease)
                resource = Lease(
                    lease_id="rs-" + secrets.token_hex(16),
                    owner=owner,
                    profile_id=profile.database_profile,
                    profile=binding.profile,
                    worker=binding.worker,
                    created_at=self.now(),
                    expires_at=lease["expires_at"],
                )
                lease["resource"] = resource.model_dump()
                if not self.checkpoint(lease):
                    return
                environment["EVANDER_APPLICATION_RESOURCE_FILE"] = (
                    "/run/evander-resources/sqlserver-addon/"
                    + resource.lease_id
                    + "/connection.json"
                )
                environment["EVANDER_APPLICATION_DATABASE_ALIAS"] = manifest.database
            envfile = folder / "app.env"
            if any("\n" in value or "\r" in value for value in environment.values()):
                raise RuntimeError("Preview environment values must be single-line")
            envfile.write_text(
                "".join(key + "=" + value + "\n" for key, value in environment.items())
            )
            envfile.chmod(0o600)
            common = [
                "--label",
                "evander.resource=" + lease["id"],
                "--network",
                lease["name"],
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                identity,
                "--pids-limit",
                "512",
                "--dns",
                "127.0.0.1",
                "--init",
                "--pull",
                "never",
                "--log-opt",
                "max-size=10m",
                "--log-opt",
                "max-file=2",
            ]
            mounts = []
            for source, target in profile.readonly_mounts.items():
                from evander.worker.sandbox_profile import validate_worker_read_only_mount
                from evander.worker.sandbox_profile import WorkerReadOnlyMount

                validate_worker_read_only_mount(
                    WorkerReadOnlyMount(host_path=source, container_path=target)
                )
                reserved = ("/workspace", "/run/browser.json", "/run/preview-output")
                if any(
                    target == path
                    or target.startswith(path + "/")
                    or path.startswith(target.rstrip("/") + "/")
                    for path in reserved
                ):
                    raise RuntimeError("Toolchain mount overlaps preview-owned paths")
                if not Path(source).is_absolute() or not Path(source).exists():
                    raise RuntimeError("Approved toolchain mount is unavailable")
                mounts += ["--mount", f"type=bind,src={source},dst={target},readonly"]
            remaining = max(1, int(lease["expires_at"] - self.now()))
            workspace = self.resolve_workspace(
                lease["session"]["agent_session_id"], lease["session"]["workspace_ref"]
            )
            descriptor = os.open(
                self.settings.worker_root, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            try:
                for component in workspace.relative_to(self.settings.worker_root.resolve()).parts:
                    next_descriptor = os.open(
                        component, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                    )
                    os.close(descriptor)
                    descriptor = next_descriptor
            except BaseException:
                os.close(descriptor)
                raise
            mount_source = folder / "workspace"
            try:
                mount_source.mkdir(mode=0o700, exist_ok=True)
                # libmount must not canonicalize the proc-fd back into an agent-
                # controlled path. The kernel bind holds the already checked inode.
                checked = os.fstat(descriptor)
                self.host_mount_command(
                    sys.executable,
                    "-B",
                    "-m",
                    "evander.resources.mount_pin",
                    "--worker-root",
                    str(self.settings.worker_root),
                    "--relative",
                    workspace.relative_to(self.settings.worker_root.resolve()).as_posix(),
                    "--target",
                    str(mount_source),
                    "--device",
                    str(checked.st_dev),
                    "--inode",
                    str(checked.st_ino),
                )
                self.docker(
                    "run",
                    "-d",
                    "--name",
                    lease["name"] + "-app",
                    *common,
                    "--ip",
                    lease["app_ip"],
                    "--network-alias",
                    lease["name"] + "-app",
                    "--cpus",
                    str(profile.cpus),
                    "--memory",
                    str(profile.memory_mib) + "m",
                    "--memory-swap",
                    str(profile.memory_mib) + "m",
                    "--env-file",
                    str(envfile),
                    "--mount",
                    f"type=bind,src={mount_source},dst=/workspace",
                    "--mount",
                    f"type=bind,src={output},dst=/run/preview-output",
                    *mounts,
                    "--tmpfs",
                    PRIVATE_TMPFS_SPEC,
                    "--mount",
                    f"type=bind,src={Path(__file__).with_name('entrypoint.py')},dst=/run/evander-entrypoint.py,readonly",
                    "--workdir",
                    "/workspace/" + manifest.working_directory,
                    "--entrypoint",
                    "/usr/bin/timeout",
                    profile.image,
                    "--signal=TERM",
                    "--kill-after=10",
                    str(remaining),
                    *(
                        ["/usr/bin/python3", "/run/evander-entrypoint.py"]
                        if manifest.database
                        else []
                    ),
                    *manifest.command,
                )
            finally:
                os.close(descriptor)
            container = self.inspect(lease["name"] + "-app")
            self.workloads.put(
                Workload(
                    id=lease["id"],
                    owner=self.owner(lease),
                    worker_id=self.settings.worker_id,
                    container_id=container["Id"],
                    expires_at=lease["expires_at"],
                )
            )
            if lease.get("resource"):
                deadline = min(self.now() + manifest.startup_seconds, lease["expires_at"])
                while self.ensure_resource(lease).get("state") != "ready":
                    if self.now() >= deadline or self.get(lease["id"])["state"] == "closing":
                        raise RuntimeError("Database startup did not become ready")
                    time.sleep(1)
            address = f"http://{lease['app_ip']}:{manifest.port}{manifest.readiness_path}"
            deadline = min(self.now() + manifest.startup_seconds, lease["expires_at"])
            while not self.probe(address):
                if self.now() >= deadline:
                    raise RuntimeError(
                        "Preview readiness deadline exceeded", "startup_timeout", 504
                    )
                if self.get(lease["id"])["state"] == "closing":
                    return
                time.sleep(1)
            if self.get(lease["id"])["state"] == "closing":
                return
            if self.get(lease["id"])["state"] != "closing":
                for item in lease["endpoints"]:
                    item.update(
                        ready=True,
                        network=lease["name"],
                        address=lease["app_ip"],
                        startup_state_path=str(output),
                    )
                    self.endpoints.put(item)
                lease["state"] = "running"
                if not self.checkpoint(lease):
                    return
        except Exception as error:
            lease["diagnostic"] = self.diagnostic(error)
            if self.get(lease["id"])["state"] != "closing":
                lease["state"], lease["error"] = (
                    "failed",
                    "Application startup failed; close to release resources",
                )
                if not self.checkpoint(lease):
                    return

    @staticmethod
    def _probe(address):
        try:
            with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
                return client.get(address).status_code < 400
        except httpx.HTTPError:
            return False

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
            try:
                self.endpoints.revoke(session_id)
                self.dependencies.release("resource:" + session_id)
                self.workloads.revoke(self.owner(lease))
                for suffix in ("app",):
                    name = lease["name"] + "-" + suffix
                    value = self.inspect(name)
                    if value:
                        if value["Config"].get("Labels", {}).get("evander.resource") != session_id:
                            raise RuntimeError("Refusing to remove an unowned container")
                        self.docker("rm", "--force", name)
                if lease.get("resource"):
                    if self.ensure_resource(lease, release=True).get("state") not in {
                        "released",
                        "expired",
                    }:
                        raise RuntimeError("SQL resource release has not been confirmed")
                network = self.inspect(lease["name"], "network")
                if network:
                    if network.get("Labels", {}).get("evander.resource") != session_id:
                        raise RuntimeError("Refusing to remove an unowned network")
                    self.docker("network", "rm", lease["name"])
                self.remove_firewall(lease)
                mount_source = self.root / session_id / "workspace"
                if mount_source.exists():
                    result = self.host_mount_command(
                        "mountpoint", "-q", "--", str(mount_source), check=False
                    )
                    if result.returncode == 0:
                        self.host_mount_command("umount", "--", str(mount_source))
                    elif result.returncode != 32:  # util-linux: present, not a mountpoint
                        raise RuntimeError("Could not verify workspace mount cleanup")
                shutil.rmtree(self.root / session_id, ignore_errors=False) if (
                    self.root / session_id
                ).exists() else None
                self.dependencies.remove(session_id + ":worker")
                self.dependencies.remove(session_id + ":workspace")
                lease["state"], lease["error"] = "closed", None
                self.save(lease)
            except Exception:
                lease["state"], lease["error"] = (
                    "cleanup_failed",
                    "Worker cleanup failed; resources remain reserved",
                )
                self.save(lease)
                raise RuntimeError(lease["error"], "cleanup_failed", 503) from None
            return {"released": True}

    def reconcile(self, *, startup=False):
        for lease in self.leases():
            if lease["state"] == "closed":
                continue
            if self.now() >= lease["expires_at"] or lease["state"] in {"closing", "cleanup_failed"}:
                self.close(lease["id"])
            elif startup and lease["state"] == "starting":
                # A crash may have run migrations. Never blindly replay startup.
                lease["state"], lease["error"] = (
                    "failed",
                    "Startup was interrupted; close and create a new preview",
                )
                self.save(lease)
            elif lease["state"] == "accepted":
                self.start(lease)
            current = self.get(lease["id"])
            if current["state"] == "running":
                for suffix in ("app",):
                    container = self.inspect(current["name"] + "-" + suffix)
                    if container and container.get("State", {}).get("Running") is True:
                        continue
                    with self.lock:
                        current = self.get(lease["id"])
                        if current["state"] == "running":
                            current["state"], current["error"] = (
                                "failed",
                                "Preview process exited; close to release resources",
                            )
                            self.save(current)
                    break
