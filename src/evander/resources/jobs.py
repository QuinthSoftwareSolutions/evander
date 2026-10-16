"""Exact worker-job binding; no caller supplied command, path, container or mount."""

import ipaddress
import json
from pathlib import Path
import socket
import subprocess
import time
import uuid

from evander.worker.store import WorkerJobStore

from .models import ResourceError
from .projection import PREPARE, PROJECT, SEAL, UNPROJECT


class Jobs:
    def __init__(self, addon_id, database, *, run=subprocess.run, jobs=None):
        self.addon_id = addon_id
        self.store = jobs or WorkerJobStore(Path(database))
        self.run = run

    def command(self, *args, data=None, check=True):
        result = self.run(list(args), input=data, capture_output=True, timeout=25)
        if check and result.returncode:
            raise ResourceError("Worker binding command failed")
        return result

    def inspect(self, container_id):
        result = self.command("docker", "container", "inspect", container_id, check=False)
        if result.returncode:
            self.command("docker", "info", "--format", "{{.ID}}")
            if b"no such" not in result.stderr.lower():
                raise ResourceError("Cannot determine worker job state")
            return None
        return json.loads(result.stdout)[0]

    def current(self, owner):
        if (
            not owner.session_id
            or owner.valid_until is not None
            and time.time() >= owner.valid_until
        ):
            return None
        found = []
        for record in self.store.list_by_session(owner.session_id):
            if record.status != "running" or any(
                record.metadata.get(k) != v for k, v in owner.job_tags.items()
            ):
                continue
            value = self.inspect(record.container_id or record.container_name)
            if value and value["State"]["Running"] and value["Id"] == record.container_id:
                found.append((record, value))
        if len(found) > 1:
            raise ResourceError("Worker job binding is ambiguous")
        return found[0] if found else None

    def bind(self, lease, job, connections, save):
        record, inspected = job
        if lease.bound_job_id != record.job_id:
            self.unbind(lease, save)
        base = "/tmp/evander-" + self.addon_id
        lease.job_binding = {
            "container_id": record.container_id,
            "base": base,
            "staging_path": "/tmp/evander-resource-" + uuid.uuid4().hex,
            "rules": [],
            "network": lease.resources.get("network"),
        }
        save()
        network = lease.resources.get("network")
        if network and network not in inspected["NetworkSettings"]["Networks"]:
            self.command("docker", "network", "connect", network, record.container_id)
            inspected = self.inspect(record.container_id)
        networks = inspected["NetworkSettings"]["Networks"]
        source = next(
            (v["IPAddress"] for k, v in networks.items() if k != network and v.get("IPAddress")),
            None,
        )
        if source:
            ipaddress.ip_address(source)
            for connection in connections.values():
                host, port = connection["host"], connection["port"]
                for resolved in socket.getaddrinfo(
                    host, port, family=socket.AF_INET, type=socket.SOCK_STREAM
                ):
                    target = str(ipaddress.ip_address(resolved[4][0]))
                    route_source = next(
                        (
                            v["IPAddress"]
                            for v in networks.values()
                            if v.get("IPAddress")
                            and ipaddress.ip_address(target)
                            in ipaddress.ip_network(
                                f"{v['IPAddress']}/{v.get('IPPrefixLen', 32)}", strict=False
                            )
                        ),
                        source,
                    )
                    rule = [
                        "-s",
                        route_source + "/32",
                        "-d",
                        target + "/32",
                        "-p",
                        "tcp",
                        "--dport",
                        str(port),
                        "-m",
                        "comment",
                        "--comment",
                        self.addon_id + ":" + lease.lease_id,
                        "-j",
                        "ACCEPT",
                    ]
                    lease.job_binding["rules"].append(rule)
                    save()
                    exists = self.command(
                        "iptables", "-w", "5", "-C", "DOCKER-USER", *rule, check=False
                    )
                    if exists.returncode:
                        self.command("iptables", "-w", "5", "-I", "DOCKER-USER", "1", *rule)
        config_path = base + "/" + lease.lease_id + "/connection.json"
        self.command(
            "docker",
            "exec",
            "--user",
            "0:0",
            record.container_id,
            "python3",
            "-c",
            PREPARE,
            base,
            lease.lease_id,
        )
        identity = (
            self.command("docker", "exec", record.container_id, "id", "-u").stdout.decode().strip()
        )
        group = (
            self.command("docker", "exec", record.container_id, "id", "-g").stdout.decode().strip()
        )
        if not identity.isdigit() or not group.isdigit() or identity == "0":
            raise ResourceError("Private resource configuration requires an unprivileged job user")
        self.command(
            "docker",
            "exec",
            "--interactive",
            record.container_id,
            "python3",
            "-c",
            PROJECT,
            base,
            "0",
            lease.job_binding["staging_path"],
            data=json.dumps(
                {"lease_id": lease.lease_id, "connections": connections, "probe_connections": True}
            ).encode(),
        )
        self.command(
            "docker",
            "exec",
            "--user",
            "0:0",
            record.container_id,
            "python3",
            "-c",
            SEAL,
            base,
            lease.lease_id,
            identity,
            group,
            lease.job_binding["staging_path"],
        )
        lease.bound_job_id, lease.config_path = record.job_id, config_path
        save()

    def unbind(self, lease, save):
        binding = lease.job_binding
        if not binding:
            lease.bound_job_id = lease.config_path = None
            return
        failed, remaining = False, []
        # Firewall withdrawal must not depend on Docker, the private file, or the
        # provider being healthy. Keep only unverified rules for the next retry.
        for rule in binding.get("rules", []):
            try:
                status = self.command(
                    "iptables", "-w", "5", "-C", "DOCKER-USER", *rule, check=False
                ).returncode
                if status not in {0, 1}:
                    raise ResourceError("Cannot verify worker network grant removal")
                if status == 0:
                    self.command("iptables", "-w", "5", "-D", "DOCKER-USER", *rule)
                    if (
                        self.command(
                            "iptables", "-w", "5", "-C", "DOCKER-USER", *rule, check=False
                        ).returncode
                        != 1
                    ):
                        raise ResourceError("Worker network grant removal is incomplete")
            except Exception:
                failed = True
                remaining.append(rule)
        binding["rules"] = remaining
        save()
        try:
            container = self.inspect(binding["container_id"])
        except Exception:
            container, failed = None, True
        if container:
            if (
                binding.get("network")
                and binding["network"] in container["NetworkSettings"]["Networks"]
            ):
                try:
                    self.command(
                        "docker",
                        "network",
                        "disconnect",
                        binding["network"],
                        binding["container_id"],
                    )
                    current = self.inspect(binding["container_id"])
                    if current and binding["network"] in current["NetworkSettings"]["Networks"]:
                        raise ResourceError("Worker network disconnection is incomplete")
                except Exception:
                    failed = True
            if container["State"]["Running"]:
                try:
                    self.command(
                        "docker",
                        "exec",
                        "--user",
                        "0:0",
                        binding["container_id"],
                        "python3",
                        "-c",
                        UNPROJECT,
                        binding["base"],
                        lease.lease_id,
                        binding.get("staging_path", ""),
                    )
                except Exception:
                    failed = True
        if failed:
            save()
            raise ResourceError("Worker binding cleanup is incomplete; durable intent retained")
        lease.job_binding = {}
        lease.bound_job_id = lease.config_path = None
        save()
