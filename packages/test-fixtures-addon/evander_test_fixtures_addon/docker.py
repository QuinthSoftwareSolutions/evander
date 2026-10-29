"""Fixed Docker operations. No caller-supplied command, mount, image or path."""

import base64
import hashlib
import ipaddress
import json
from pathlib import Path
import secrets
import subprocess

from .models import FixtureError, NotReady


# Root prepares and seals directories only. The capability-dropped job creates
# its own mode0600 file, so neither it nor the root helper needs CAP_CHOWN.
PREPARE = r"""
import os, stat, sys
base, lease=sys.argv[1:3]
for path in [base, base+'/'+lease]:
    try:
        os.mkdir(path, 0o711)
        os.chmod(path, 0o711)
    except FileExistsError: pass
    st=os.lstat(path)
    allowed={0o700, 0o711, 0o733} if path != base else {0o700, 0o711}
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) not in allowed:
        raise RuntimeError('Unsafe fixture directory')
    if stat.S_IMODE(st.st_mode) == 0o700:
        os.chmod(path, 0o711)
directory=base+'/'+lease
try: os.unlink(directory+'/connection.new')
except FileNotFoundError: pass
os.chmod(directory, 0o733)
"""
PROJECT = r"""
import json, os, socket, stat, sys
data=json.load(sys.stdin)
base, owner=sys.argv[1], int(sys.argv[2])
for path, mode in [(base, 0o711), (base+'/'+data['lease_id'], 0o733)]:
    st=os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != owner or stat.S_IMODE(st.st_mode) != mode:
        raise RuntimeError('Unsafe fixture directory')
directory=base+'/'+data['lease_id']
temporary=directory+'/connection.new'
fd=os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data['connections'], stream)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, directory+'/connection.json')
finally:
    if os.path.lexists(temporary): os.unlink(temporary)
if data.get('probe_connections'):
    for connection in data['connections'].values():
        host=connection.get('server') or connection['host']
        with socket.create_connection((host, connection['port']), timeout=3): pass
"""
SEAL = r"""
import os, stat, sys
base, lease, uid, gid=sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
for path in [base, base+'/'+lease]:
    st=os.lstat(path)
    allowed={0o711, 0o733} if path != base else {0o711}
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) not in allowed:
        raise RuntimeError('Unsafe fixture directory')
directory=base+'/'+lease
os.chmod(directory, 0o711)
path=directory+'/connection.json'
st=os.lstat(path)
if not stat.S_ISREG(st.st_mode) or st.st_uid != uid or st.st_gid != gid or stat.S_IMODE(st.st_mode) != 0o600:
    raise RuntimeError('Unsafe fixture configuration')
"""
UNPROJECT = r"""
import os, stat, sys
path=sys.argv[1]
if not os.path.lexists(path): sys.exit(0)
for component in [sys.argv[2]]:
    st=os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o022: raise RuntimeError('Unsafe fixture directory')
    path+='/'+component
if os.path.lexists(path):
    st=os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) not in {0o700, 0o711, 0o733}: raise RuntimeError('Unsafe fixture directory')
    os.chmod(path, 0o711)
    for name in ['connection.json','connection.new']:
        try: os.unlink(path+'/'+name)
        except FileNotFoundError: pass
    os.rmdir(path)
"""


class Docker:
    def __init__(self, root, image, *, run=subprocess.run):
        self.root = Path(root)
        self.image = image
        self.run = run

    def command(self, *args, data=None, check=True, timeout=25):
        try:
            result = self.run(list(args), input=data, capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FixtureError("Worker fixture command unavailable or timed out") from exc
        if check and result.returncode:
            # Never expose subprocess stderr: SQL, broker and Docker diagnostics
            # can contain credentials, host paths or operator configuration.
            raise FixtureError("Worker fixture command failed")
        return result

    def docker(self, *args, **kw):
        return self.command("docker", *args, **kw)

    def inspect(self, name, kind="container"):
        result = self.docker(kind, "inspect", name, check=False)
        if result.returncode:
            # Distinguish missing resources from a stopped/unreachable daemon.
            self.docker("info", "--format", "{{.ID}}")
            error = result.stderr.lower().strip()
            missing_network = kind == "network" and error.endswith(
                f"network {name} not found".lower().encode()
            )
            if b"no such" not in error and not missing_network:
                raise FixtureError("Docker resource inspection failed")
            return None
        return json.loads(result.stdout)[0]

    def current_job(self, record):
        value = self.inspect(record.container_id or record.container_name)
        return (
            value
            if value and value["State"]["Running"] and value["Id"] == record.container_id
            else None
        )

    def ensure_broker(self, lease):
        name = lease["lease_id"]
        folder = self.root / name
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.image:
            raise FixtureError("No approved RabbitMQ image configured")
        # inspect only; allocation never pulls an image or contacts a registry.
        self.docker("image", "inspect", self.image)
        network = self.inspect(name, "network")
        if network is None:
            self.docker(
                "network", "create", "--internal", "--label", "evander.fixture=" + name, name
            )
        elif (
            not network.get("Internal") or network.get("Labels", {}).get("evander.fixture") != name
        ):
            raise FixtureError("Fixture network ownership mismatch")
        lease["resource_counts"]["networks"] = 1
        broker = self.inspect(name)
        if broker is None:
            volume = self.inspect(name, "volume")
            if volume is None:
                self.docker("volume", "create", "--label", "evander.fixture=" + name, name)
            elif volume.get("Labels", {}).get("evander.fixture") != name:
                raise FixtureError("Fixture volume ownership mismatch")
            lease["resource_counts"]["volumes"] = 1
            password = lease["secrets"]["rabbitmq_password"]
            salt = secrets.token_bytes(4)
            password_hash = base64.b64encode(
                salt + hashlib.sha256(salt + password.encode()).digest()
            ).decode()
            definitions = {
                "users": [
                    {
                        "name": "fixture",
                        "password_hash": password_hash,
                        "hashing_algorithm": "rabbit_password_hashing_sha256",
                        "tags": [],
                    }
                ],
                "vhosts": [{"name": name}],
                "permissions": [
                    {
                        "user": "fixture",
                        "vhost": name,
                        "configure": ".*",
                        "write": ".*",
                        "read": ".*",
                    }
                ],
                "policies": [
                    {
                        "vhost": name,
                        "name": "fixture-bounds",
                        "pattern": ".*",
                        "apply-to": "queues",
                        "definition": {
                            "max-length-bytes": 16 * 1024 * 1024,
                            "overflow": "reject-publish",
                        },
                        "priority": 100,
                    }
                ],
            }
            (folder / "definitions.json").write_text(json.dumps(definitions))
            (folder / "rabbitmq.conf").write_text(
                "definitions.import_backend = local_filesystem\n"
                "definitions.local.path = /etc/rabbitmq/definitions.json\n"
                "loopback_users.guest = true\n"
                "vm_memory_high_watermark.absolute = 512MiB\n"
                "disk_free_limit.absolute = 50MB\n"
                "log.console = true\nlog.file = false\n"
            )
            # The host directory is root-only, but the two exact bind mounts
            # must be readable by the unprivileged broker UID after umask0077.
            (folder / "definitions.json").chmod(0o444)
            (folder / "rabbitmq.conf").chmod(0o444)
            self.docker(
                "create",
                "--pull=never",
                "--name",
                name,
                "--hostname",
                name,
                "--label",
                "evander.fixture=" + name,
                "--network",
                name,
                "--memory",
                "1g",
                "--memory-swap",
                "1g",
                "--cpus",
                "1",
                "--pids-limit",
                "256",
                "--user",
                "999:999",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--env",
                "RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS=+S 2:2 +SDcpu 1 +SDio 1",
                "--env",
                "RABBITMQ_CTL_ERL_ARGS=+S 1:1 +A 2",
                "--read-only",
                "--mount",
                f"type=volume,src={name},dst=/var/lib/rabbitmq",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=64m,uid=999,gid=999",
                "--tmpfs",
                "/var/log/rabbitmq:rw,nosuid,nodev,size=16m,uid=999,gid=999",
                "--log-driver",
                "local",
                "--log-opt",
                "max-size=10m",
                "--log-opt",
                "max-file=2",
                "--mount",
                f"type=bind,src={folder / 'definitions.json'},dst=/etc/rabbitmq/definitions.json,readonly",
                "--mount",
                f"type=bind,src={folder / 'rabbitmq.conf'},dst=/etc/rabbitmq/rabbitmq.conf,readonly",
                self.image,
            )
            broker = self.inspect(name)
        lease["resource_counts"]["brokers"] = 1
        if (
            broker["Config"].get("Labels", {}).get("evander.fixture") != name
            or broker["Config"]["Image"] != self.image
        ):
            raise FixtureError("Fixture container ownership or image mismatch")
        if not broker["State"]["Running"]:
            self.docker("start", name)
        # Check protocol readiness, including imports and local resource alarms.
        result = self.docker(
            "exec",
            name,
            "rabbitmq-diagnostics",
            "-q",
            "-t",
            "5",
            "check_running",
            check=False,
            timeout=8,
        )
        if result.returncode:
            raise NotReady("RabbitMQ is starting")
        self.docker(
            "exec", name, "rabbitmq-diagnostics", "-q", "-t", "5", "check_local_alarms", timeout=8
        )
        self.docker(
            "exec",
            name,
            "rabbitmqctl",
            "-q",
            "-t",
            "5",
            "set_vhost_limits",
            "--vhost",
            name,
            '{"max-connections":32,"max-queues":32}',
            timeout=8,
        )
        return self.inspect(name)["NetworkSettings"]["Networks"][name]["IPAddress"]

    def bind(self, lease, job, connections):
        if "rabbitmq" in lease["profile"]:
            network = lease["lease_id"]
            inspected = self.current_job(job)
            if not inspected:
                raise FixtureError("Owning worker job is no longer running")
            if network not in inspected["NetworkSettings"]["Networks"]:
                self.docker("network", "connect", network, job.container_id)
            inspected = self.current_job(job)
            job_ip = inspected["NetworkSettings"]["Networks"][network]["IPAddress"]
            self.firewall(lease, job_ip)
        uid = int(self.docker("exec", job.container_id, "/usr/bin/id", "-u").stdout)
        gid = int(self.docker("exec", job.container_id, "/usr/bin/id", "-g").stdout)
        if uid == 0:
            raise FixtureError("Fixtures require an unprivileged owning worker job")
        base = "/tmp/evander-test-fixtures"
        lease_id = lease["lease_id"]
        self.docker(
            "exec",
            "--user",
            "0",
            job.container_id,
            "/usr/bin/python3",
            "-I",
            "-S",
            "-c",
            PREPARE,
            base,
            lease_id,
        )
        try:
            # Only this unprivileged process receives credentials. Root-owned
            # ancestors cannot be replaced by the job, even during this write.
            self.docker(
                "exec",
                "--interactive",
                job.container_id,
                "/usr/bin/python3",
                "-I",
                "-S",
                "-c",
                PROJECT,
                base,
                "0",
                data=json.dumps(
                    dict(lease_id=lease_id, connections=connections, probe_connections=True)
                ).encode(),
            )
        finally:
            # Revoke directory writes on success and failure before returning.
            # This also verifies the completed file without reading its secrets.
            self.docker(
                "exec",
                "--user",
                "0",
                job.container_id,
                "/usr/bin/python3",
                "-I",
                "-S",
                "-c",
                SEAL,
                base,
                lease_id,
                str(uid),
                str(gid),
            )

    def unbind(self, lease):
        if "rabbitmq" in lease["profile"]:
            self.firewall(lease, None)
        container = lease.get("bound_container_id")
        if container:
            inspected = self.inspect(container)
            if inspected and inspected["State"]["Running"]:
                self.docker(
                    "exec",
                    "--user",
                    "0",
                    container,
                    "/usr/bin/python3",
                    "-I",
                    "-S",
                    "-c",
                    UNPROJECT,
                    "/tmp/evander-test-fixtures",
                    lease["lease_id"],
                )
                if lease["lease_id"] in inspected["NetworkSettings"]["Networks"]:
                    self.docker("network", "disconnect", "--force", lease["lease_id"], container)

    def firewall(self, lease, job_ip):
        # A per-lease internal bridge has no external route. This chain also
        # denies cross-lease/host-routed access to the broker. It survives core
        # egress policy reapplication because it owns a separate chain.
        broker_ip = lease.get("broker_ip")
        if not broker_ip:
            return
        broker_ip = str(ipaddress.IPv4Address(broker_ip))
        chain = "EVFX_" + lease["lease_id"][3:23]

        def ipt(*args, **kw):
            return self.command("iptables", "-w", "5", *args, **kw)

        ipt("-N", chain, check=False)
        # Install the jump once. A flush leaves an empty (fail-closed internal
        # network) interval; the following deny rules precede each permit.
        if ipt("-C", "DOCKER-USER", "-j", chain, check=False).returncode:
            ipt("-I", "DOCKER-USER", "1", "-j", chain)
        ipt("-F", chain)
        if job_ip:
            job_ip = str(ipaddress.IPv4Address(job_ip))
            ipt(
                "-A",
                chain,
                "-s",
                job_ip,
                "-d",
                broker_ip,
                "-p",
                "tcp",
                "--dport",
                "5672",
                "-j",
                "ACCEPT",
            )
            ipt(
                "-A",
                chain,
                "-s",
                broker_ip,
                "-d",
                job_ip,
                "-p",
                "tcp",
                "--sport",
                "5672",
                "-m",
                "conntrack",
                "--ctstate",
                "ESTABLISHED",
                "-j",
                "ACCEPT",
            )
        ipt("-A", chain, "-d", broker_ip, "-j", "DROP")
        ipt("-A", chain, "-s", broker_ip, "-j", "DROP")

    def control(self, lease, action):
        self.docker(
            {"start": "start", "stop": "stop", "pause": "pause", "resume": "unpause"}[action],
            lease["lease_id"],
        )

    def recover(self, lease):
        inspected = self.inspect(lease["lease_id"])
        if inspected:
            if inspected["State"].get("Paused"):
                self.docker("unpause", lease["lease_id"])
            if not inspected["State"]["Running"]:
                self.docker("start", lease["lease_id"])

    def remove_broker(self, lease):
        name = lease["lease_id"]
        inspected = self.inspect(name)
        if inspected:
            if inspected["Config"].get("Labels", {}).get("evander.fixture") != name:
                raise FixtureError("Refusing to remove an unowned container")
            if inspected["State"].get("Paused"):
                self.docker("unpause", name)
            self.docker("rm", "--force", name)
        network = self.inspect(name, "network")
        if network:
            if network.get("Labels", {}).get("evander.fixture") != name:
                raise FixtureError("Refusing to remove an unowned network")
            self.docker("network", "rm", name)
        volume = self.inspect(name, "volume")
        if volume:
            if volume.get("Labels", {}).get("evander.fixture") != name:
                raise FixtureError("Refusing to remove an unowned volume")
            self.docker("volume", "rm", name)
        chain = "EVFX_" + name[3:23]
        if self.command("iptables", "-w", "5", "-S", chain, check=False).returncode == 0:
            self.command("iptables", "-w", "5", "-D", "DOCKER-USER", "-j", chain, check=False)
            self.command("iptables", "-w", "5", "-F", chain)
            self.command("iptables", "-w", "5", "-X", chain)
        folder = self.root / name
        for filename in ("definitions.json", "rabbitmq.conf"):
            (folder / filename).unlink(missing_ok=True)
        if folder.exists():
            folder.rmdir()
