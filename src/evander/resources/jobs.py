"""Exact worker-job binding; no caller supplied command, path, container or mount."""

import ipaddress
import json
from pathlib import Path, PurePosixPath
import socket
import subprocess
import time
import uuid

from evander.worker.store import WorkerJobStore

from .models import ResourceError
from .projection import PREPARE, PROJECT, SEAL, UNPROJECT, PRIVATE_TMPFS, RESOURCE_CHAIN, ROUTE


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

    def exec_job(self, container_id, *program, root=False, data=None):
        flags = ["docker", "exec", "--workdir", "/"]
        # Docker must clear loader hooks before starting even /usr/bin/env.
        for name in ("LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT"):
            flags.extend(["--env", name + "="])
        if root:
            flags.extend(["--user", "0:0"])
        if data is not None:
            flags.append("--interactive")
        return self.command(
            *flags, container_id, "/usr/bin/env", "-i", "PATH=/usr/bin:/bin", *program, data=data
        )

    def python(self, container_id, script, *arguments, root=False, data=None):
        return self.exec_job(
            container_id,
            "/usr/bin/python3",
            "-I",
            "-S",
            "-c",
            script,
            *arguments,
            root=root,
            data=data,
        )

    def route_source(self, record, inspected, target, port):
        source = str(
            ipaddress.IPv4Address(
                self.python(record.container_id, ROUTE, target, str(port)).stdout.decode().strip()
            )
        )
        if not any(
            source == network.get("IPAddress")
            for network in inspected["NetworkSettings"]["Networks"].values()
        ):
            raise ResourceError("Destination route does not belong to the authorized job")
        return source

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
        base = PRIVATE_TMPFS + "/" + self.addon_id
        if not self.ephemeral(inspected, base):
            raise ResourceError("Resource credentials require the worker's private tmpfs")
        if lease.bound_job_id != record.job_id:
            self.unbind(lease, save)
        lease.job_binding = {
            "container_id": record.container_id,
            "base": base,
            "staging_path": PRIVATE_TMPFS + "/evander-resource-" + uuid.uuid4().hex,
            "rule_chain": RESOURCE_CHAIN,
            "rules": [],
            "network": lease.resources.get("network"),
        }
        save()
        network = lease.resources.get("network")
        if network and network not in inspected["NetworkSettings"]["Networks"]:
            self.command("docker", "network", "connect", network, record.container_id)
            inspected = self.inspect(record.container_id)
        for connection in connections.values():
            host, port = connection["host"], connection["port"]
            for resolved in socket.getaddrinfo(
                host, port, family=socket.AF_INET, type=socket.SOCK_STREAM
            ):
                target = str(ipaddress.ip_address(resolved[4][0]))
                route_source = self.route_source(record, inspected, target, port)
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
                self.allow_rule(RESOURCE_CHAIN, rule)
        config_path = base + "/" + lease.lease_id + "/connection.json"
        self.python(
            record.container_id,
            PREPARE,
            base,
            lease.lease_id,
            root=True,
        )
        identity = self.exec_job(record.container_id, "/usr/bin/id", "-u").stdout.decode().strip()
        group = self.exec_job(record.container_id, "/usr/bin/id", "-g").stdout.decode().strip()
        if not identity.isdigit() or not group.isdigit() or identity == "0":
            raise ResourceError("Private resource configuration requires an unprivileged job user")
        self.python(
            record.container_id,
            PROJECT,
            base,
            "0",
            lease.job_binding["staging_path"],
            data=json.dumps(
                {"lease_id": lease.lease_id, "connections": connections, "probe_connections": True}
            ).encode(),
        )
        self.python(
            record.container_id,
            SEAL,
            base,
            lease.lease_id,
            identity,
            group,
            lease.job_binding["staging_path"],
            root=True,
        )
        lease.bound_job_id, lease.config_path = record.job_id, config_path
        save()

    @staticmethod
    def ephemeral(container, path):
        path = PurePosixPath(path)
        mounts = container.get("HostConfig", {}).get("Tmpfs") or {}
        ephemeral = [PurePosixPath(root) for root in mounts if path.is_relative_to(root)]
        if not ephemeral:
            return False
        root = max(ephemeral, key=lambda item: len(item.parts))
        # A nested persistent mount must not hide the ephemeral storage. Treat
        # unknown mounts conservatively; these records come from Docker, not a job.
        return not any(
            mount.get("Type") != "tmpfs"
            and (destination := PurePosixPath(mount["Destination"])).is_relative_to(root)
            and (destination.is_relative_to(path) or path.is_relative_to(destination))
            for mount in container.get("Mounts", [])
        )

    def allow_rule(self, chain, rule):
        status = self.command("iptables", "-w", "5", "-C", chain, *rule, check=False).returncode
        if status not in {0, 1}:
            raise ResourceError("Cannot verify worker network grant")
        if status == 1:
            if chain == RESOURCE_CHAIN:
                self.command("iptables", "-w", "5", "-N", chain, check=False)
            self.command("iptables", "-w", "5", "-I", chain, "1", *rule)

    def refresh(self, lease, job, save):
        record, inspected = job
        binding = lease.job_binding
        addresses = {
            value["IPAddress"]
            for value in inspected["NetworkSettings"]["Networks"].values()
            if value.get("IPAddress")
        }
        if (
            binding.get("container_id") != record.container_id
            or binding.get("rule_chain") != RESOURCE_CHAIN
            or not self.ephemeral(inspected, binding["base"])
            or any(
                rule[rule.index("-s") + 1].removesuffix("/32") not in addresses
                for rule in binding["rules"]
            )
        ):
            self.unbind(lease, save)
            raise ResourceError("Worker binding requires revalidation")
        for rule in binding["rules"]:
            self.allow_rule(RESOURCE_CHAIN, rule)

    def unbind(self, lease, save):
        binding = lease.job_binding
        if not binding:
            lease.bound_job_id = lease.config_path = None
            return
        failed, remaining = False, []
        chain = binding.get("rule_chain", "DOCKER-USER")
        # Firewall withdrawal must not depend on Docker, the private file, or the
        # provider being healthy. Keep only unverified rules for the next retry.
        for rule in binding.get("rules", []):
            try:
                status = self.command(
                    "iptables", "-w", "5", "-C", chain, *rule, check=False
                ).returncode
                if status not in {0, 1}:
                    raise ResourceError("Cannot verify worker network grant removal")
                if status == 0:
                    self.command("iptables", "-w", "5", "-D", chain, *rule)
                    if (
                        self.command(
                            "iptables", "-w", "5", "-C", chain, *rule, check=False
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
                    self.python(
                        binding["container_id"],
                        UNPROJECT,
                        binding["base"],
                        lease.lease_id,
                        binding.get("staging_path", ""),
                        root=True,
                    )
                except Exception:
                    failed = True
            elif not (
                self.ephemeral(container, binding["base"])
                and (
                    not binding.get("staging_path")
                    or self.ephemeral(container, binding["staging_path"])
                )
            ):
                # A stopped container's writable layer survives. Do not claim
                # withdrawal until it can be cleaned while running or is removed.
                failed = True
        if failed:
            save()
            raise ResourceError("Worker binding cleanup is incomplete; durable intent retained")
        lease.job_binding = {}
        lease.bound_job_id = lease.config_path = None
        save()
