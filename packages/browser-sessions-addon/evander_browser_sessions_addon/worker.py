"""Private, durable worker supervisor. No agent-supplied host paths or Docker options."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
from urllib.parse import urlsplit

import httpx
from pydantic import Field

from .models import (
    BrowserError,
    Contract,
    LaunchManifest,
    LaunchProfile,
    Session,
    identifier,
    relative_path,
)
from evander.session_dependencies import SessionDependencies


class WorkerSettings(Contract):
    worker_id: str
    worker_root: Path
    runtime_dir: Path
    token_env: str = "EVANDER_BROWSER_WORKER_TOKEN"
    allocations_enabled: bool = False
    callback_url: str = "http://127.0.0.1:11891"
    max_sessions: int = Field(default=1, ge=1, le=32)
    profiles: dict[str, LaunchProfile] = Field(default_factory=dict)
    relay_urls: list[str] = Field(default_factory=list)
    sql_server: str | None = None
    sql_admin_user: str = "sa"
    sql_admin_password_env: str = "EVANDER_BROWSER_SQL_ADMIN_PASSWORD"
    sqlcmd: str = "/opt/mssql-tools18/bin/sqlcmd"


def contained(root: Path, path: Path) -> Path:
    try:
        root, path = root.resolve(strict=True), path.resolve(strict=True)
    except OSError:
        raise BrowserError("Workspace is unavailable", "workspace_invalid", 409) from None
    if path == root or root not in path.parents:
        raise BrowserError("Workspace path escaped its owning session", "workspace_invalid", 409)
    return path


def bounded_json(path: Path, size=65536):
    if not path.is_file() or path.stat().st_size > size:
        raise BrowserError("Launch file is unavailable or too large", "manifest_invalid", 409)
    return json.loads(path.read_text())


class Supervisor:
    def __init__(self, settings: WorkerSettings, *, command=None, now=time.time, probe=None):
        self.settings, self.now = settings, now
        self.command = command or self._command
        self.probe = probe or self._probe
        self.lock = threading.RLock()
        self.operations = {}
        self.root = settings.runtime_dir / "browser-sessions"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.dependencies = SessionDependencies(settings.worker_root)
        self.dbpath = self.root / "leases.db"
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS leases(id TEXT PRIMARY KEY,payload TEXT)")
        self.dbpath.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.dbpath, timeout=10)
        try:
            yield db
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _command(*argv, check=True, env=None):
        try:
            return subprocess.run(
                list(argv), check=check, capture_output=True, text=True, timeout=45, env=env
            )
        except (subprocess.SubprocessError, OSError):
            raise BrowserError(
                "Worker resource operation failed; inspect private diagnostics",
                "worker_operation_failed",
                503,
            ) from None

    def docker(self, *args, **kwargs):
        return self.command("docker", *args, **kwargs)

    def inspect(self, name, kind="container"):
        result = self.docker(kind, "inspect", name, check=False)
        if result.returncode:
            # Missing is distinguishable from a stopped Docker daemon.
            self.docker("info", "--format", "{{.ServerVersion}}")
            return None
        return json.loads(result.stdout)[0]

    def save(self, lease):
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO leases VALUES(?,?)", (lease["id"], json.dumps(lease))
            )

    def get(self, session_id):
        identifier(session_id)
        with self.db() as db:
            row = db.execute("SELECT payload FROM leases WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise BrowserError("Unknown worker browser session", "not_found", 404)
        return json.loads(row[0])

    def leases(self):
        with self.db() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT payload FROM leases")]

    def session_root(self, parent_id):
        identifier(parent_id)
        base = self.settings.worker_root / "sessions"
        return contained(base, base / parent_id)

    def workspaces(self, parent_id):
        root = self.session_root(parent_id)
        home = root / "agent_home"
        if not home.exists():
            return {"workspaces": []}
        # Enumerate only repository roots, never arbitrary home or artifact mounts.
        candidates = []
        repos = home / "repos"
        if repos.is_dir():
            candidates.extend(p for p in repos.iterdir() if p.is_dir() and (p / ".git").exists())
        receipts = root / "task-worktree-preparation-receipts"
        if receipts.is_dir():
            for receipt in receipts.glob("*.json"):
                if receipt.is_symlink():
                    continue
                value = bounded_json(receipt)
                target = value.get("target_dir", "")
                if value.get("version") != 1 or not target.startswith("/home/agent/"):
                    continue
                candidates.append(home / target.removeprefix("/home/agent/"))
        result = []
        for candidate in candidates:
            try:
                actual = contained(home, candidate)
                if not (actual / ".git").exists():
                    continue
                relative = actual.relative_to(home).as_posix()
                ref = hashlib.sha256((parent_id + "\0" + relative).encode()).hexdigest()[:32]
                result.append({"ref": ref, "path": "/home/agent/" + relative})
            except (OSError, ValueError):
                continue
        return {"workspaces": list({item["ref"]: item for item in result}.values())}

    def resolve_workspace(self, parent_id, ref):
        for item in self.workspaces(parent_id)["workspaces"]:
            if secrets.compare_digest(item["ref"], ref):
                return contained(
                    self.session_root(parent_id) / "agent_home",
                    self.session_root(parent_id)
                    / "agent_home"
                    / item["path"].removeprefix("/home/agent/"),
                )
        raise BrowserError(
            "Workspace reference does not belong to this agent", "workspace_invalid", 409
        )

    def create(self, payload):
        session = Session.model_validate(payload["session"])
        profile = LaunchProfile.model_validate(payload["profile"])
        if not self.settings.allocations_enabled:
            raise BrowserError("Worker preview allocations are disabled", "disabled", 409)
        if session.worker_id != self.settings.worker_id or session.mode == "user_hosted":
            raise BrowserError("Worker assignment mismatch", "worker_mismatch", 409)
        approved = self.settings.profiles.get(session.profile_id)
        if approved is None or approved != profile:
            raise BrowserError(
                "Launch profile differs from the worker-approved profile", "profile_mismatch", 409
            )
        if not self.now() < session.expires_at <= self.now() + 86400:
            raise BrowserError("Invalid preview lease")
        relay = str(payload["relay_url"])
        if relay not in self.settings.relay_urls:
            raise BrowserError("Browser relay is not approved on this worker", "relay_denied", 403)
        with self.lock:
            try:
                old = self.get(session.id)
            except BrowserError as exc:
                if exc.status != 404:
                    raise
                old = None
            if old:
                if old["session"] != session.model_dump() or old["profile"] != profile.model_dump():
                    raise BrowserError("Preview identity conflict", "identity_conflict", 409)
                return self.receipt(old)
            active = [item for item in self.leases() if item["state"] != "closed"]
            if len(active) >= self.settings.max_sessions or any(
                item["session"]["agent_session_id"] == session.agent_session_id for item in active
            ):
                raise BrowserError("Worker preview quota reached", "quota_exceeded", 409)
            workspace = self.resolve_workspace(session.agent_session_id, session.workspace_ref)
            manifest_path = contained(
                workspace, workspace / relative_path(session.launch_manifest_path)
            )
            manifest = LaunchManifest.model_validate(bounded_json(manifest_path))
            if manifest.database and (
                not self.settings.sql_server
                or not os.environ.get(self.settings.sql_admin_password_env)
            ):
                raise BrowserError(
                    "Preview database service is not configured", "database_unavailable", 409
                )
            if manifest.database and not profile.database_enabled:
                raise BrowserError(
                    "This profile does not permit a database", "database_denied", 403
                )
            contained(
                workspace, workspace / manifest.working_directory
            ) if manifest.working_directory != "." else workspace
            name = "evbs-" + session.id.removeprefix("browser-")
            origins = [
                f"http://{name}-app:{port}" for port in [manifest.port, *manifest.additional_ports]
            ]
            lease = {
                "id": session.id,
                "name": name,
                "state": "accepted",
                "session": session.model_dump(),
                "profile": profile.model_dump(),
                "workspace": str(workspace),
                "manifest": manifest.model_dump(),
                "allowed_origins": origins,
                "relay_url": relay,
                "endpoint_token": payload["endpoint_token"],
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
                self.settings.callback_url.rstrip("/") + "/v1/sessions/" + session.id + "/close"
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
            self.save(lease)
            return self.receipt(lease)

    def receipt(self, lease):
        return {
            "id": lease["id"],
            "state": lease["state"],
            "allowed_origins": lease["allowed_origins"],
            "workspace_path": lease["workspace_path"],
            "error": lease["error"],
        }

    def ipt(self, *args, **kwargs):
        return self.command("iptables", "-w", "5", *args, **kwargs)

    def firewall(self, lease):
        subnet = str(ipaddress.IPv4Network(lease["subnet"]))
        chain = "EVBS_" + lease["name"][5:23]
        # A private bridge plus filtering in FORWARD and INPUT. Bridge filtering
        # must exist before containers can run; no fail-open fallback.
        self.command("sysctl", "-w", "net.bridge.bridge-nf-call-iptables=1")
        self.ipt("-N", chain, check=False)
        for parent in ("DOCKER-USER", "INPUT"):
            if self.ipt("-C", parent, "-j", chain, check=False).returncode:
                self.ipt("-I", parent, "1", "-j", chain)
        self.ipt("-F", chain)
        self.ipt(
            "-A",
            chain,
            "-s",
            subnet,
            "-m",
            "conntrack",
            "--ctstate",
            "ESTABLISHED,RELATED",
            "-j",
            "ACCEPT",
        )
        self.ipt("-A", chain, "-s", subnet, "-d", subnet, "-j", "ACCEPT")
        for source, host, port in lease["egress"]:
            self.ipt(
                "-A",
                chain,
                "-s",
                source,
                "-d",
                str(ipaddress.IPv4Address(host)),
                "-p",
                "tcp",
                "--dport",
                str(port),
                "-j",
                "ACCEPT",
            )
        self.ipt("-A", chain, "-s", subnet, "-j", "DROP")
        self.ipt(
            "-A",
            chain,
            "-d",
            subnet,
            "-m",
            "conntrack",
            "--ctstate",
            "ESTABLISHED,RELATED",
            "-j",
            "ACCEPT",
        )
        self.ipt("-A", chain, "-d", subnet, "-j", "DROP")

    def remove_firewall(self, lease):
        chain = "EVBS_" + lease["name"][5:23]
        if self.ipt("-S", chain, check=False).returncode == 0:
            for parent in ("DOCKER-USER", "INPUT"):
                if self.ipt("-C", parent, "-j", chain, check=False).returncode == 0:
                    self.ipt("-D", parent, "-j", chain)
            self.ipt("-F", chain)
            self.ipt("-X", chain)

    def _sql(self, statement):
        settings = self.settings
        password = os.environ.get(settings.sql_admin_password_env)
        if not settings.sql_server or not password:
            raise BrowserError(
                "Preview database service is unavailable", "database_unavailable", 503
            )
        self.command(
            settings.sqlcmd,
            "-S",
            settings.sql_server,
            "-U",
            settings.sql_admin_user,
            "-b",
            "-C",
            "-Q",
            statement,
            env={**os.environ, "SQLCMDPASSWORD": password},
        )

    def provision_database(self, lease):
        # Names/password are generated by the supervisor, never SQL identifiers
        # from a repository or agent. Admin credentials remain in the worker.
        name = "preview_" + lease["name"].removeprefix("evbs-")
        password = lease.setdefault("database_password", secrets.token_urlsafe(32) + "Aa1!")
        lease["database_name"] = name
        self.save(lease)
        self._sql(
            f"IF DB_ID('{name}') IS NULL CREATE DATABASE [{name}]; "
            f"IF SUSER_ID('{name}') IS NULL CREATE LOGIN [{name}] WITH PASSWORD='{password}'; "
            f"USE [{name}]; IF USER_ID('{name}') IS NULL CREATE USER [{name}] FOR LOGIN [{name}]; "
            f"ALTER ROLE db_owner ADD MEMBER [{name}];"
        )
        return f"Server={self.settings.sql_server};Database={name};User Id={name};Password={password};TrustServerCertificate=True"

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
            self.save(lease)
        try:
            profile, manifest = (
                LaunchProfile.model_validate(lease["profile"]),
                LaunchManifest.model_validate(lease["manifest"]),
            )
            folder = self.root / lease["id"]
            folder.mkdir(mode=0o700, exist_ok=True)
            output = folder / "output"
            output.mkdir(mode=0o700, exist_ok=True)
            os.chown(output, 1000, 1000)
            self.docker(
                "network",
                "create",
                "--label",
                "evander.browser=" + lease["id"],
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
            parsed = urlsplit(lease["relay_url"])
            relay_ip = socket.gethostbyname(parsed.hostname)
            egress = [
                (
                    lease["browser_ip"],
                    relay_ip,
                    parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80),
                )
            ]
            for target in profile.egress_allowlist:
                address, port = target.rsplit(":", 1)
                address, port = str(ipaddress.IPv4Address(address)), int(port)
                if not 1 <= port <= 65535:
                    raise BrowserError("Invalid approved egress port")
                egress.append((lease["app_ip"], address, port))
            lease["egress"] = egress
            self.save(lease)
            self.firewall(lease)
            environment = {
                **profile.public_environment,
                "EVANDER_PREVIEW_ORIGIN": lease["allowed_origins"][0],
                "EVANDER_PREVIEW_ENTRY_URL": lease["allowed_origins"][0] + manifest.entry_path,
                "EVANDER_PREVIEW_OUTPUT": "/run/preview-output",
                "EVANDER_PREVIEW_SESSION_ID": lease["id"],
            }
            if manifest.database:
                environment["EVANDER_PREVIEW_DATABASE"] = self.provision_database(lease)
            envfile = folder / "app.env"
            if any("\n" in value or "\r" in value for value in environment.values()):
                raise BrowserError("Preview environment values must be single-line")
            envfile.write_text(
                "".join(key + "=" + value + "\n" for key, value in environment.items())
            )
            envfile.chmod(0o600)
            common = [
                "--label",
                "evander.browser=" + lease["id"],
                "--network",
                lease["name"],
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                "1000:1000",
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
                    raise BrowserError("Toolchain mount overlaps preview-owned paths")
                if not Path(source).is_absolute() or not Path(source).exists():
                    raise BrowserError("Approved toolchain mount is unavailable")
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
            mount_source = f"/proc/{os.getpid()}/fd/{descriptor}"
            try:
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
                    "--workdir",
                    "/workspace/" + manifest.working_directory,
                    "--entrypoint",
                    "/usr/bin/timeout",
                    profile.image,
                    "--signal=TERM",
                    "--kill-after=10",
                    str(remaining),
                    *manifest.command,
                )
            finally:
                os.close(descriptor)
            address = f"http://{lease['app_ip']}:{manifest.port}{manifest.readiness_path}"
            deadline = min(self.now() + manifest.startup_seconds, lease["expires_at"])
            while not self.probe(address):
                if self.now() >= deadline:
                    raise BrowserError(
                        "Preview readiness deadline exceeded", "startup_timeout", 504
                    )
                if self.get(lease["id"])["state"] == "closing":
                    return
                time.sleep(1)
            if self.get(lease["id"])["state"] == "closing":
                return
            state = dict(lease["session"], allowed_origins=lease["allowed_origins"])
            config = {
                "relay_url": lease["relay_url"],
                "endpoint_token": lease["endpoint_token"],
                "allowed_origins": lease["allowed_origins"],
                "session": state,
                "entry_url": lease["allowed_origins"][0] + manifest.entry_path,
            }
            configfile = folder / "browser.json"
            configfile.write_text(json.dumps(config))
            # Mounted as an individual file into a separate unprivileged container.
            configfile.chmod(0o444)
            self.docker(
                "run",
                "-d",
                "--name",
                lease["name"] + "-browser",
                *common,
                "--ip",
                lease["browser_ip"],
                "--add-host",
                parsed.hostname + ":" + relay_ip,
                "--memory",
                str(profile.browser_memory_mib) + "m",
                "--cpus",
                "1",
                "--env",
                "EVANDER_BROWSER_CONNECTION=/run/browser.json",
                "--mount",
                f"type=bind,src={configfile},dst=/run/browser.json,readonly",
                "--mount",
                f"type=bind,src={output},dst=/run/preview-output,readonly",
                *mounts,
                profile.browser_image,
            )
            if self.get(lease["id"])["state"] != "closing":
                lease["state"] = "running"
                self.save(lease)
        except Exception:
            if self.get(lease["id"])["state"] != "closing":
                lease["state"], lease["error"] = (
                    "failed",
                    "Preview startup failed; close to release resources",
                )
                self.save(lease)

    @staticmethod
    def _probe(address):
        try:
            with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
                return client.get(address).status_code < 500
        except httpx.HTTPError:
            return False

    def close(self, session_id):
        with self.lock:
            operation = self.operations.setdefault(session_id, threading.RLock())
            try:
                lease = self.get(session_id)
            except BrowserError as exc:
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
            except BrowserError as exc:
                if exc.status == 404:
                    return {"released": True}
                raise
            if lease["state"] == "closed":
                return {"released": True}
            lease["state"] = "closing"
            self.save(lease)
            try:
                for suffix in ("browser", "app"):
                    name = lease["name"] + "-" + suffix
                    value = self.inspect(name)
                    if value:
                        if value["Config"].get("Labels", {}).get("evander.browser") != session_id:
                            raise BrowserError("Refusing to remove an unowned container")
                        self.docker("rm", "--force", name)
                network = self.inspect(lease["name"], "network")
                if network:
                    if network.get("Labels", {}).get("evander.browser") != session_id:
                        raise BrowserError("Refusing to remove an unowned network")
                    self.docker("network", "rm", lease["name"])
                self.remove_firewall(lease)
                if lease.get("database_name"):
                    name = lease["database_name"]
                    self._sql(
                        f"IF DB_ID('{name}') IS NOT NULL BEGIN ALTER DATABASE [{name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [{name}]; END; IF SUSER_ID('{name}') IS NOT NULL DROP LOGIN [{name}];"
                    )
                shutil.rmtree(self.root / session_id, ignore_errors=False) if (
                    self.root / session_id
                ).exists() else None
                self.dependencies.remove(session_id + ":worker")
                self.dependencies.remove(session_id + ":workspace")
                lease["state"], lease["error"] = "closed", None
                lease.pop("endpoint_token", None)
                lease.pop("database_password", None)
                self.save(lease)
            except Exception:
                lease["state"], lease["error"] = (
                    "cleanup_failed",
                    "Worker cleanup failed; resources remain reserved",
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
                # A crash may have run migrations. Never blindly replay startup.
                lease["state"], lease["error"] = (
                    "failed",
                    "Startup was interrupted; close and create a new preview",
                )
                self.save(lease)
            elif lease["state"] == "accepted":
                self.start(lease)
