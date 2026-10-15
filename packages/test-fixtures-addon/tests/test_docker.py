import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from evander_test_fixtures_addon.docker import Docker, PROJECT, UNPROJECT
from evander_test_fixtures_addon.models import FixtureError

LEASE = "fx-" + "a" * 32
IMAGE = "rabbitmq@sha256:" + "b" * 64


def test_broker_uses_internal_network_pinned_image_and_recoverable_private_volume(tmp_path):
    calls = []
    resources = {}

    def run(args, **kw):
        calls.append((args, kw))
        rest = args[1:]
        kind = rest[0]
        if len(rest) > 1 and rest[1] == "inspect":
            if kind == "image":
                return SimpleNamespace(returncode=0, stdout=b"[{}]", stderr=b"")
            value = resources.get(kind)
            return SimpleNamespace(
                returncode=0 if value else 1,
                stdout=json.dumps([value]).encode() if value else b"",
                stderr=b"No such object",
            )
        if rest[:2] in [["network", "create"], ["volume", "create"]]:
            resources[kind] = {"Internal": True, "Labels": {"evander.fixture": LEASE}}
        if kind == "create":
            resources["container"] = {
                "Config": {"Labels": {"evander.fixture": LEASE}, "Image": IMAGE},
                "State": {"Running": True},
                "NetworkSettings": {"Networks": {LEASE: {"IPAddress": "198.51.100.2"}}},
            }
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    docker = Docker(tmp_path, IMAGE, run=run)
    lease = {
        "lease_id": LEASE,
        "profile": "rabbitmq",
        "secrets": {"rabbitmq_password": "synthetic-private-password"},
        "resource_counts": {},
    }
    assert docker.ensure_broker(lease) == "198.51.100.2"
    assert docker.ensure_broker(lease) == "198.51.100.2"
    create = [args for args, _ in calls if args[1] == "create"]
    assert len(create) == 1
    args = create[0]
    assert args[-1] == IMAGE and "--pull=never" in args
    assert "--publish" not in args and "-p" not in args
    assert args[args.index("--memory") + 1] == "1g"
    assert args[args.index("--cpus") + 1] == "1"
    assert args[args.index("--pids-limit") + 1] == "256"
    assert f"type=volume,src={LEASE},dst=/var/lib/rabbitmq" in args
    assert "synthetic-private-password" not in json.dumps(calls)
    assert not any("pull" == item for args, _ in calls for item in args)
    definitions = json.loads((tmp_path / LEASE / "definitions.json").read_text())
    assert definitions["vhosts"] == [{"name": LEASE}]
    assert "password" not in definitions["users"][0]
    assert definitions["users"][0]["name"] == "fixture"


def test_subprocess_failure_and_timeout_never_reflect_stderr_or_arguments(tmp_path):
    def fail(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout=b"private-output", stderr=b"password=private")

    docker = Docker(tmp_path, IMAGE, run=fail)
    with pytest.raises(FixtureError) as exc:
        docker.command("docker", "exec", "synthetic")
    assert "private" not in str(exc.value)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(["private"], 5)

    docker.run = timeout
    with pytest.raises(FixtureError) as exc:
        docker.command("docker", "info")
    assert "private" not in str(exc.value)


def project(base, payload):
    return subprocess.run(
        [sys.executable, "-I", "-S", "-c", PROJECT, str(base)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=5,
        cwd=base.parent,
    )


def test_private_projection_is_atomic_recovers_stale_temporary_and_revokes(tmp_path):
    base = tmp_path / "private"
    (tmp_path / "json.py").write_text("raise RuntimeError('untrusted repository code ran')")
    payload = dict(
        lease_id=LEASE,
        uid=os.getuid(),
        gid=os.getgid(),
        connections={"source": {"password": "synthetic"}},
    )
    assert project(base, payload).returncode == 0
    path = base / LEASE / "connection.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text()) == payload["connections"]
    (base / LEASE / "connection.new").write_text("incomplete crash write")
    assert project(base, payload).returncode == 0
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-c", UNPROJECT, str(base), LEASE],
            capture_output=True,
            timeout=5,
        )
        assert result.returncode == 0
    assert not path.exists()


@pytest.mark.parametrize("attack", ["parent-symlink", "lease-symlink", "writable-parent"])
def test_projection_rejects_agent_controlled_paths_without_overwriting_target(tmp_path, attack):
    base = tmp_path / "private"
    target = tmp_path / "agent-home"
    target.mkdir()
    sentinel = target / "connection.json"
    sentinel.write_text("keep")
    if attack == "parent-symlink":
        base.symlink_to(target)
    else:
        base.mkdir(mode=0o711)
        if attack == "lease-symlink":
            (base / LEASE).symlink_to(target)
        else:
            base.chmod(0o777)
    result = project(
        base,
        dict(lease_id=LEASE, uid=os.getuid(), gid=os.getgid(), connections={"password": "secret"}),
    )
    assert result.returncode != 0
    assert sentinel.read_text() == "keep"


def test_firewall_permits_only_owning_job_to_broker_amqp(tmp_path):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    docker = Docker(tmp_path, IMAGE, run=run)
    docker.firewall({"lease_id": LEASE, "broker_ip": "198.51.100.1"}, "198.51.100.2")
    accepts = [args for args in calls if args[-1] == "ACCEPT"]
    assert len(accepts) == 2
    assert all("198.51.100.1" in args and "198.51.100.2" in args for args in accepts)
    assert any("--dport" in args and "5672" in args for args in accepts)
    assert len([args for args in calls if args[-1] == "DROP"]) == 2


def test_privileged_projection_uses_isolated_absolute_interpreter_and_stdin(tmp_path):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"1001\n", stderr=b"")

    docker = Docker(tmp_path, IMAGE, run=run)
    docker.bind(
        {"lease_id": LEASE, "profile": "sqlserver"},
        SimpleNamespace(container_id="b" * 64),
        {"source": {"password": "private-secret", "server": "192.0.2.1", "port": 1433}},
    )
    args, kwargs = calls[-1]
    assert "/usr/bin/python3" in args
    assert "-I" in args and "-S" in args
    assert "private-secret" not in " ".join(args)
    assert json.loads(kwargs["input"])["probe_connections"] is True
