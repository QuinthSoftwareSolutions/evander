import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from evander_test_fixtures_addon.docker import Docker, PREPARE, PROJECT, SEAL, UNPROJECT
from evander_test_fixtures_addon.models import FixtureError

LEASE = "fx-" + "a" * 32
IMAGE = "rabbitmq@sha256:" + "b" * 64


@pytest.mark.parametrize(
    "network_missing_error",
    [
        f"Error: No such network: {LEASE}".encode(),
        f"Error response from daemon: network {LEASE} not found\n".encode(),
    ],
)
def test_broker_uses_internal_network_pinned_image_and_recoverable_private_volume(
    tmp_path, network_missing_error
):
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
                stderr=network_missing_error if kind == "network" else b"No such object",
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


def test_cleanup_succeeds_when_a_failed_allocation_never_created_its_network(tmp_path):
    calls = []
    folder = tmp_path / LEASE
    folder.mkdir()
    (folder / "rabbitmq.conf").write_text("synthetic configuration")

    def run(args, **kw):
        calls.append(args)
        if args[:3] == ["docker", "network", "inspect"]:
            return SimpleNamespace(
                returncode=1,
                stdout=b"[]\n",
                stderr=f"Error response from daemon: network {LEASE} not found\n".encode(),
            )
        if len(args) > 2 and args[2] == "inspect":
            return SimpleNamespace(returncode=1, stdout=b"[]", stderr=b"No such object")
        if args[0] == "iptables":
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"Chain does not exist")
        return SimpleNamespace(returncode=0, stdout=b"daemon-id", stderr=b"")

    docker = Docker(tmp_path, IMAGE, run=run)
    for _ in range(2):
        docker.remove_broker({"lease_id": LEASE})
    assert not folder.exists()
    assert not any("rm" in args for args in calls)


@pytest.mark.parametrize(
    "error,daemon_available",
    [
        (f"Error response from daemon: network {LEASE} not found\n".encode(), False),
        (b"Error response from daemon: network other-network not found\n", True),
        (b"Error response from daemon: configuration file not found\n", True),
        (b"permission denied", True),
    ],
)
def test_network_inspection_does_not_hide_daemon_or_unrelated_errors(
    tmp_path, error, daemon_available
):
    def run(args, **kw):
        if args[1] == "info":
            return SimpleNamespace(
                returncode=0 if daemon_available else 1, stdout=b"", stderr=b"daemon unavailable"
            )
        return SimpleNamespace(returncode=1, stdout=b"[]", stderr=error)

    with pytest.raises(FixtureError):
        Docker(tmp_path, IMAGE, run=run).inspect(LEASE, "network")


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


def helper(script, base, *args, payload=None):
    return subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, str(base), *map(str, args)],
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        timeout=5,
        cwd=base.parent,
        umask=0o077,
    )


def project(base, payload):
    prepared = helper(PREPARE, base, LEASE)
    if prepared.returncode:
        return prepared
    result = helper(PROJECT, base, os.getuid(), payload=payload)
    sealed = helper(SEAL, base, LEASE, os.getuid(), os.getgid())
    return sealed if result.returncode == 0 else result


@pytest.mark.parametrize("existing_private_dirs", [False, True])
def test_private_projection_is_atomic_recovers_stale_temporary_and_revokes(
    tmp_path, existing_private_dirs
):
    base = tmp_path / "private"
    if existing_private_dirs:
        base.mkdir(mode=0o700)
        (base / LEASE).mkdir(mode=0o700)
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
    assert path.parent.stat().st_mode & 0o777 == 0o711
    assert path.stat().st_uid == os.getuid()
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


@pytest.mark.parametrize("write_fails", [False, True])
def test_credentials_are_created_by_job_user_without_adding_capabilities(tmp_path, write_fails):
    calls = []
    base = tmp_path / "private"

    def run(args, **kwargs):
        calls.append((args, kwargs))
        assert "--privileged" not in args and "--cap-add" not in args
        if "/usr/bin/id" in args:
            value = 1001 if args[-1] == "-u" else 1002
            return SimpleNamespace(returncode=0, stdout=f"{value}\n".encode(), stderr=b"")
        command = args[args.index("-c") + 1]
        if command == PROJECT:
            # A root exec under the worker's empty capability bounding set
            # cannot chown a credential file to this unprivileged job.
            assert "--user" not in args
            if write_fails:
                raise OSError("simulated job write failure")
            payload = json.loads(kwargs["input"])
            payload["probe_connections"] = False
            return helper(command, base, os.getuid(), payload=payload)
        assert kwargs["input"] is None  # Root helpers never receive credentials.
        assert args[args.index("--user") + 1] == "0"
        if command == SEAL:
            assert args[-2:] == ["1001", "1002"]
            # Emulate the two container identities with this process's UID/GID
            # so this filesystem test also runs on root and non-root CI hosts.
            return helper(command, base, LEASE, os.getuid(), os.getgid())
        extra = args[args.index("-c") + 3 :]
        return helper(command, base, *extra)

    docker = Docker(tmp_path, IMAGE, run=run)
    args = (
        {"lease_id": LEASE, "profile": "sqlserver"},
        SimpleNamespace(container_id="b" * 64),
        {"source": {"password": "private-secret", "server": "192.0.2.1", "port": 1433}},
    )
    if write_fails:
        with pytest.raises(FixtureError):
            docker.bind(*args)
    else:
        docker.bind(*args)
    for args, _ in calls:
        assert "private-secret" not in " ".join(args)
        if "-c" in args:
            assert "/usr/bin/python3" in args and "-I" in args and "-S" in args
    secret_calls = [(args, kw) for args, kw in calls if kw["input"]]
    assert len(secret_calls) == 1
    assert json.loads(secret_calls[0][1]["input"])["probe_connections"] is True
    path = base / LEASE / "connection.json"
    assert path.parent.stat().st_mode & 0o777 == 0o711
    if write_fails:
        assert not path.exists()
        return
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.stat().st_uid == os.getuid()
    assert json.loads(path.read_text())["source"]["password"] == "private-secret"


def test_failed_projection_seals_the_directory_and_crash_cleanup_revokes_it(tmp_path):
    base = tmp_path / "private"
    assert helper(PREPARE, base, LEASE).returncode == 0
    # An interrupted or rejected job write must not leave the directory writable.
    (base / LEASE / "connection.new").write_text("interrupted write")
    result = helper(SEAL, base, LEASE, os.getuid(), os.getgid())
    assert result.returncode != 0  # Missing final file cannot be reported ready.
    assert (base / LEASE).stat().st_mode & 0o777 == 0o711
    # A worker crash before the seal must also remain cleanable without chown.
    assert helper(PREPARE, base, LEASE).returncode == 0
    assert (base / LEASE).stat().st_mode & 0o777 == 0o733
    assert helper(UNPROJECT, base, LEASE).returncode == 0
    assert not (base / LEASE).exists()
