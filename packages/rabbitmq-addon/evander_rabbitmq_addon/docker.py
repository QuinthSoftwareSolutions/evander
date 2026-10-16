"""Fixed, bounded Docker operations for dedicated disposable brokers."""

import json
from pathlib import Path
import secrets
import subprocess
import time

from evander.resources.models import ResourceError, NotReady

from .adapter import password_hash


class Brokers:
    def __init__(self, root, images, *, run=subprocess.run):
        self.root, self.images, self.run = Path(root), set(images), run

    def command(self, *args, check=True, timeout=25):
        result = self.run(list(args), capture_output=True, timeout=timeout)
        if check and result.returncode:
            raise ResourceError("Broker worker command failed")
        return result

    def docker(self, *args, **kwargs):
        return self.command("docker", *args, **kwargs)

    def inspect(self, name, kind="container"):
        value = self.docker(kind, "inspect", name, check=False)
        if value.returncode == 0:
            return json.loads(value.stdout)[0]
        self.docker("info", "--format", "{{.ID}}")
        if b"no such" not in value.stderr.lower() and b"not found" not in value.stderr.lower():
            raise ResourceError("Broker resource state is unavailable")
        return None

    def check(self, value, marker, kind):
        labels = (
            value.get("Config", {}).get("Labels", {})
            if kind == "container"
            else value.get("Labels", {})
        )
        if labels.get("evander.resource-owner") != marker:
            raise ResourceError("Broker resource ownership differs; preserved")

    def ensure(self, lease, save):
        image = lease.profile.options["image"]
        if image not in self.images:
            raise ResourceError("RabbitMQ image is not approved on this worker")
        self.docker("image", "inspect", image)
        r, name = lease.resources, lease.lease_id
        if "admin_password" not in r:
            r.update(
                admin_user="evander_admin",
                admin_password=secrets.token_urlsafe(32),
                network=name,
                starting_at=time.time(),
            )
            save()
        marker = r["marker"]
        network = self.inspect(name, "network")
        if network:
            self.check(network, marker, "network")
            if not network["Internal"]:
                raise ResourceError("Broker network must be internal")
        else:
            self.docker(
                "network",
                "create",
                "--internal",
                "--label",
                "evander.resource-owner=" + marker,
                name,
            )
        volume = self.inspect(name, "volume")
        if volume:
            self.check(volume, marker, "volume")
        else:
            self.docker("volume", "create", "--label", "evander.resource-owner=" + marker, name)
        container = self.inspect(name)
        if container:
            self.check(container, marker, "container")
            if container["Config"]["Image"] != image:
                raise ResourceError("Broker image differs from the approved lease image")
        else:
            r["starting_at"] = time.time()
            r["recovering"] = True
            save()
            folder = self.root / name
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            files = {
                "definitions.json": json.dumps(
                    {
                        "users": [
                            {
                                "name": r["admin_user"],
                                "password_hash": password_hash(r["admin_password"]),
                                "hashing_algorithm": "rabbit_password_hashing_sha256",
                                "tags": ["administrator"],
                            }
                        ]
                    }
                ),
                "rabbitmq.conf": "definitions.import_backend = local_filesystem\ndefinitions.local.path = /etc/rabbitmq/definitions.json\nloopback_users.guest = true\nvm_memory_high_watermark.absolute = 512MiB\ndisk_free_limit.absolute = 50MB\nlog.console = true\nlog.file = false\n",
                "enabled_plugins": "[rabbitmq_management].\n",
            }
            for filename, data in files.items():
                path = folder / filename
                path.write_text(data)
                path.chmod(0o444)
            mounts = [
                part
                for filename in files
                for part in (
                    "--mount",
                    f"type=bind,src={folder / filename},dst=/etc/rabbitmq/{filename},readonly",
                )
            ]
            self.docker(
                "create",
                "--pull=never",
                "--name",
                name,
                "--hostname",
                name,
                "--label",
                "evander.resource-owner=" + marker,
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
                *mounts,
                image,
            )
            container = self.inspect(name)
        if not container["State"]["Running"]:
            r["starting_at"] = time.time()
            r["recovering"] = True
            save()
            self.docker("start", name)
        if self.docker(
            "exec",
            name,
            "rabbitmq-diagnostics",
            "-q",
            "-t",
            "5",
            "check_running",
            check=False,
            timeout=8,
        ).returncode:
            if time.time() - r["starting_at"] >= 120:
                raise ResourceError("RabbitMQ startup deadline exceeded")
            raise NotReady("RabbitMQ is starting; reconciliation will retry")
        self.docker(
            "exec", name, "rabbitmq-diagnostics", "-q", "-t", "5", "check_local_alarms", timeout=8
        )
        ip = self.inspect(name)["NetworkSettings"]["Networks"][name]["IPAddress"]
        r["connection"] = {
            "management_url": f"http://{ip}:15672",
            "host": ip,
            "port": 5672,
            "tls": False,
        }
        save()

    def control(self, lease, action):
        value = self.inspect(lease.lease_id)
        if value is None:
            raise ResourceError("Broker is absent")
        self.check(value, lease.resources["marker"], "container")
        if action in {"start", "resume"}:
            self.recover(lease)
            return
        if (
            action == "pause"
            and value["State"].get("Paused")
            or action == "stop"
            and not value["State"]["Running"]
        ):
            return
        command = {"start": "start", "resume": "unpause", "stop": "stop", "pause": "pause"}[action]
        if action == "resume" and not value["State"].get("Paused"):
            return
        if action == "start" and value["State"].get("Paused"):
            self.docker("unpause", lease.lease_id)
        self.docker(command, lease.lease_id)
        state = self.inspect(lease.lease_id)["State"]
        if action == "stop" and state["Running"] or action == "pause" and not state.get("Paused"):
            raise ResourceError("Broker control has not been acknowledged")

    def recover(self, lease):
        value = self.inspect(lease.lease_id)
        if value:
            self.check(value, lease.resources["marker"], "container")
            if value["State"].get("Paused"):
                self.docker("unpause", lease.lease_id)
            if not value["State"]["Running"]:
                lease.resources["starting_at"] = time.time()
                lease.resources["recovering"] = True
                self.docker("start", lease.lease_id)

    def remove(self, lease):
        for kind in ("container", "network", "volume"):
            value = self.inspect(lease.lease_id, kind)
            if value:
                self.check(value, lease.resources["marker"], kind)
                if kind == "container":
                    if value["State"].get("Paused"):
                        self.docker("unpause", lease.lease_id)
                    self.docker("rm", "--force", lease.lease_id)
                else:
                    self.docker(kind, "rm", lease.lease_id)
            if self.inspect(lease.lease_id, kind) is not None:
                raise ResourceError("Broker cleanup is incomplete")
        folder = self.root / lease.lease_id
        for filename in ("definitions.json", "rabbitmq.conf", "enabled_plugins"):
            (folder / filename).unlink(missing_ok=True)
        if folder.exists():
            folder.rmdir()
