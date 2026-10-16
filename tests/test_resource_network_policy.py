"""Exercise resource grants with the rendered worker policy, without NET_ADMIN."""

import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from jinja2 import Environment, StrictUndefined

from evander.resources.jobs import Jobs
from evander.resources.models import Lease, Owner, Profile, WorkerSettings
from evander.resources.projection import PRIVATE_TMPFS, RESOURCE_CHAIN
from evander.resources.worker import WorkerService


# Stateful command double for the iptables CLI used by the production binding
# helper and rendered shell script. Packet verdicts below are evaluated separately.
IPTABLES = r"""
import json, os, shlex, sys
path=os.environ['TEST_FIREWALL_STATE']
with open(path) as stream: chains=json.load(stream)
args=sys.argv[1:]
if args[:1] == ['-w']: args=args[2:]
action, chain=args[:2]
rule=args[2:]
code=0
if action == '-N':
    if chain in chains: code=1
    else: chains[chain]=[]
elif chain not in chains: code=1
elif action == '-F': chains[chain]=[]
elif action == '-S':
    print('-N '+chain)
    for item in chains[chain]: print(shlex.join(['-A',chain,*item]))
elif action == '-C': code=int(rule not in chains[chain])
elif action == '-A': chains[chain].append(rule)
elif action == '-I': chains[chain].insert(int(rule[0])-1,rule[1:])
elif action == '-D':
    if rule in chains[chain]: chains[chain].remove(rule)
    else: code=1
else: raise RuntimeError('Unexpected iptables operation')
with open(path,'w') as stream: json.dump(chains,stream)
sys.exit(code)
"""


def verdict(chains, source, destination, port, chain="DOCKER-USER"):
    for rule in chains[chain]:

        def value(flag):
            return rule[rule.index(flag) + 1] if flag in rule else None

        if value("-s") and ipaddress.ip_address(source) not in ipaddress.ip_network(value("-s")):
            continue
        if value("-d") and ipaddress.ip_address(destination) not in ipaddress.ip_network(
            value("-d")
        ):
            continue
        if value("--dport") and value("--dport") != str(port):
            continue
        target = value("-j")
        if target in {"ACCEPT", "DROP"}:
            return target
        if target == "RETURN":
            return None
        result = verdict(chains, source, destination, port, target)
        if result:
            return result
    return None


def test_policy_reload_and_ready_tick_preserve_only_current_job_grants(tmp_path, monkeypatch):
    state = tmp_path / "firewall.json"
    state.write_text(json.dumps({"DOCKER-USER": [["-j", "RETURN"]]}))
    binary = tmp_path / "bin"
    binary.mkdir()
    for name, body in (
        ("iptables", IPTABLES),
        ("docker", "raise RuntimeError('Unexpected Docker call')"),
    ):
        path = binary / name
        path.write_text("#!" + sys.executable + "\n" + body)
        path.chmod(0o700)
    env = {
        **os.environ,
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "TEST_FIREWALL_STATE": str(state),
    }
    template = (
        Path(__file__).resolve().parents[1]
        / "ansible/roles/worker_container_egress_policy/templates/evander-worker-egress-policy.sh.j2"
    ).read_text()
    policy = tmp_path / "policy.sh"
    policy.write_text(
        Environment(undefined=StrictUndefined)
        .from_string(template)
        .render(
            evander_worker_docker_network="jobs",
            evander_worker_egress_policy_chain="EVANDER-WORKER-EGRESS",
            evander_worker_egress_policy_source_cidrs=["172.24.0.0/24"],
            evander_worker_egress_policy_block_cidrs=["169.254.0.0/16"],
            evander_worker_effective_egress_policy_allow_tcp_endpoints=[
                {"host": "192.0.2.1", "port": 8000}
            ],
        )
    )

    def apply():
        subprocess.run(
            ["bash", str(policy), "apply"], env=env, capture_output=True, check=True, timeout=15
        )

    def access(source="172.24.0.10", target="192.0.2.30", port=1433):
        return verdict(json.loads(state.read_text()), source, target, port)

    apply()
    assert access() == "DROP"
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[0] == "iptables":
            return subprocess.run(args, env=env, **kwargs)
        assert args[:2] == ["docker", "exec"]
        output = b"1000\n" if args[-1] in {"-u", "-g"} else b""
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr=b"")

    record = SimpleNamespace(
        job_id="job-a", status="running", metadata={}, container_id="container-a"
    )
    inspected = {
        "Id": "container-a",
        "State": {"Running": True},
        "HostConfig": {"Tmpfs": {PRIVATE_TMPFS: "rw"}},
        "NetworkSettings": {"Networks": {"jobs": {"IPAddress": "172.24.0.10", "IPPrefixLen": 24}}},
    }
    jobs = Jobs(
        "sample-addon",
        tmp_path / "jobs.db",
        jobs=SimpleNamespace(list_by_session=lambda _: [record]),
        run=run,
    )
    monkeypatch.setattr(jobs, "inspect", lambda _: inspected)

    class Adapter:
        def validate_profile(self, profile):
            pass

        def provision(self, lease, save, credentials):
            lease.resources = {"owned": True}
            save()

        def private_connections(self, lease):
            return {"app": {"host": "192.0.2.30", "port": 1433}}

    service = WorkerService(
        "sample-addon",
        WorkerSettings(
            runtime_dir=str(tmp_path / "resource"),
            worker_id="worker-a",
            token_env="TEST_TOKEN",
            allocations_enabled=True,
            connections={"local": {}},
        ),
        Adapter(),
        jobs=jobs,
        now=lambda: 1000,
    )
    lease = Lease(
        lease_id="rs-" + "a" * 32,
        owner=Owner(key="session-a", session_id="session-a", directory="root"),
        profile_id="test",
        profile=Profile(connection_id="local", worker_id="worker-a"),
        created_at=1000,
        expires_at=8200,
    )
    assert service.ensure(lease)["state"] == "ready" and access() == "ACCEPT"
    assert access(source="172.24.0.11") == "DROP" and access(port=5672) == "DROP"
    apply()
    assert access() == "ACCEPT"
    # A lost dynamic rule is repaired without reaching an unavailable provider.
    chains = json.loads(state.read_text())
    chains[RESOURCE_CHAIN] = []
    state.write_text(json.dumps(chains))
    assert access() == "DROP"
    service.adapter.provision = lambda *args: (_ for _ in ()).throw(
        AssertionError("Ready-job refresh must not reprovision")
    )
    service.tick()
    assert service.store.get(lease.lease_id).state == "ready" and access() == "ACCEPT"
    # The policy's explicit blocked ranges precede even a registered resource grant.
    jobs.allow_rule(
        RESOURCE_CHAIN,
        [
            "-s",
            "172.24.0.10/32",
            "-d",
            "169.254.169.254/32",
            "-p",
            "tcp",
            "--dport",
            "80",
            "-j",
            "ACCEPT",
        ],
    )
    assert access(target="169.254.169.254", port=80) == "DROP"
    # A changed job address must revoke the old grant rather than repairing it
    # for whichever container subsequently acquires that address.
    inspected["NetworkSettings"]["Networks"]["jobs"]["IPAddress"] = "172.24.0.11"
    service.tick()
    assert service.store.get(lease.lease_id).bound_job_id is None
    assert access() == "DROP" and access(source="172.24.0.11") == "DROP"
    # Reusing the IP after job retirement must not resurrect its old access.
    record.status = "completed"
    service.tick()
    assert access() == "DROP"
