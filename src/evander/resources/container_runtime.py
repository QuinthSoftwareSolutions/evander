"""Trusted container primitives shared by optional managed workloads."""

from contextlib import contextmanager
import hashlib
import ipaddress
import json
from pathlib import Path
import secrets
import sqlite3
import subprocess
import threading
import time
import httpx
from pydantic import Field, field_validator
from .models import Contract, ResourceError
from evander.session_dependencies import SessionDependencies


class RuntimeError(ResourceError):
    def __init__(self, message, code="worker_operation_failed", status=409):
        super().__init__(message)
        self.code, self.status = code, status


def identifier(value):
    import re

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise RuntimeError("Invalid identifier")
    return value


def relative_path(value):
    from pathlib import PurePosixPath

    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value:
        raise RuntimeError("Expected a relative workspace path")
    return str(path)


class ContainerSettings(Contract):
    worker_id: str
    worker_root: Path
    runtime_dir: Path
    token_env: str = "EVANDER_WORKLOAD_TOKEN"
    allocations_enabled: bool = False
    callback_url: str = "http://127.0.0.1:11891"
    max_sessions: int = Field(default=1, ge=1, le=32)
    container_user: str = "1000:1000"

    @field_validator("container_user")
    @classmethod
    def unprivileged_identity(cls, value):
        parts = value.split(":")
        if len(parts) != 2 or any(
            not part.isascii() or not part.isdigit() or not 1 <= int(part) < 2**32 - 1
            for part in parts
        ):
            raise ValueError("Expected an unprivileged numeric UID:GID")
        return ":".join(str(int(part)) for part in parts)


def contained(root: Path, path: Path) -> Path:
    try:
        root, path = root.resolve(strict=True), path.resolve(strict=True)
    except OSError:
        raise RuntimeError("Workspace is unavailable", "workspace_invalid", 409) from None
    if path == root or root not in path.parents:
        raise RuntimeError("Workspace path escaped its owning session", "workspace_invalid", 409)
    return path


def bounded_json(path: Path, size=65536):
    if not path.is_file() or path.stat().st_size > size:
        raise RuntimeError("Launch file is unavailable or too large", "manifest_invalid", 409)
    return json.loads(path.read_text())


class ContainerSupervisor:
    namespace = "managed-workloads"
    label = "evander.resource"

    def __init__(self, settings: ContainerSettings, *, command=None, now=time.time, probe=None):
        self.settings, self.now = settings, now
        self.command = command or self._command
        self.probe = probe or self._probe
        self.lock = threading.RLock()
        self.operations = {}
        self.root = settings.runtime_dir / self.namespace
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
            raise RuntimeError(
                "Worker resource operation failed; inspect private diagnostics",
                "worker_operation_failed",
                503,
            ) from None

    @staticmethod
    def diagnostic(error):
        """Private code location only; exception text can contain credentials."""
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        last = frames[-1] if frames else None
        return {
            "type": type(error).__name__,
            "file": Path(last.filename).name if last else None,
            "line": last.lineno if last else None,
        }

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

    def checkpoint(self, lease):
        """Persist startup side effects without overwriting concurrent cleanup intent."""
        with self.lock:
            closing = self.get(lease["id"])["state"] == "closing"
            if closing:
                lease["state"] = "closing"
            self.save(lease)
            return not closing

    def get(self, session_id):
        identifier(session_id)
        with self.db() as db:
            row = db.execute("SELECT payload FROM leases WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise RuntimeError("Unknown worker managed resource", "not_found", 404)
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
        raise RuntimeError(
            "Workspace reference does not belong to this agent", "workspace_invalid", 409
        )

    def host_mount_command(self, *args, **kwargs):
        # Docker resolves bind sources in the host mount namespace. PrivateTmp
        # places the supervisor in another namespace, so pin/cleanup explicitly there.
        return self.command(
            "nsenter", "--target=1", "--mount", "--root", "--wd", "--", *args, **kwargs
        )

    def ipt(self, *args, **kwargs):
        return self.command("iptables", "-w", "5", *args, **kwargs)

    def firewall(self, lease):
        subnet = str(ipaddress.IPv4Network(lease["subnet"]))
        chain = "EVR_" + hashlib.sha256(lease["id"].encode()).hexdigest()[:20]
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
        from .projection import RESOURCE_CHAIN

        self.ipt("-N", RESOURCE_CHAIN, check=False)
        self.ipt("-A", chain, "-j", RESOURCE_CHAIN)
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
        chain = "EVR_" + hashlib.sha256(lease["id"].encode()).hexdigest()[:20]
        if self.ipt("-S", chain, check=False).returncode == 0:
            for parent in ("DOCKER-USER", "INPUT"):
                if self.ipt("-C", parent, "-j", chain, check=False).returncode == 0:
                    self.ipt("-D", parent, "-j", chain)
            self.ipt("-F", chain)
            self.ipt("-X", chain)

    @staticmethod
    def _probe(address):
        try:
            with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
                return client.get(address).status_code < 400
        except httpx.HTTPError:
            return False
