import json
from types import SimpleNamespace

import pytest

from evander_browser_sessions_addon.models import BrowserError
from evander_browser_sessions_addon.worker import Supervisor, WorkerSettings


class Docker:
    def __init__(self):
        self.calls, self.resources, self.fail_remove = [], {}, False
        self.mounts, self.fail_unmount = set(), False
        self.fail_mount_check = False

    def __call__(self, *args, check=True, env=None):
        self.calls.append(args)
        if args[0] == "nsenter":
            command = args[args.index("--") + 1:]
            if "evander_browser_sessions_addon.mount_pin" in command:
                self.mounts.add(command[command.index("--target") + 1])
                return SimpleNamespace(returncode=0, stdout="")
            if command[0] == "mountpoint":
                return SimpleNamespace(returncode=1 if self.fail_mount_check else 0 if command[-1] in self.mounts else 32, stdout="")
            if command[0] == "mount" and "--bind" in command:
                self.mounts.add(command[-1])
            if command[0] == "umount":
                if self.fail_unmount:
                    raise BrowserError("unmount failed")
                self.mounts.discard(command[-1])
            return SimpleNamespace(returncode=0, stdout="")
        if args[0] != "docker":
            return SimpleNamespace(returncode=1 if "-C" in args or "-S" in args else 0, stdout="")
        cmd = args[1:]
        result = SimpleNamespace(returncode=0, stdout="{}")
        if cmd[:2] == ("network", "create"):
            name = cmd[-1]
            self.resources[name] = {
                "Labels": {"evander.browser": cmd[cmd.index("--label") + 1].split("=")[1]},
                "IPAM": {"Config": [{"Subnet": "172.30.22.0/24"}]},
            }
        elif cmd[0] == "run":
            name = cmd[cmd.index("--name") + 1]
            self.resources[name] = {
                "State": {"Running": True},
                "Config": {
                    "Labels": {"evander.browser": cmd[cmd.index("--label") + 1].split("=")[1]}
                }
            }
        elif len(cmd) > 1 and cmd[1] == "inspect":
            name = cmd[2]
            result.stdout = json.dumps([self.resources[name]]) if name in self.resources else ""
            result.returncode = 0 if name in self.resources else 1
        elif cmd[0] == "rm" or cmd[:2] == ("network", "rm"):
            if self.fail_remove:
                raise BrowserError("remove failed")
            self.resources.pop(cmd[-1], None)
        return result


@pytest.fixture
def worker(tmp_path, domain, hosted):
    service, _, _ = domain
    workspace = tmp_path / "worker/sessions/agent-a/agent_home/repos/application"
    (workspace / ".git").mkdir(parents=True)
    (workspace / ".evander").mkdir()
    (workspace / ".evander/preview.json").write_text(
        json.dumps({"command": ["node", "server.mjs"], "port": 4200, "additional_ports": [5000]})
    )
    docker = Docker()
    settings = WorkerSettings(
        worker_id="worker-a",
        worker_root=tmp_path / "worker",
        runtime_dir=tmp_path / "worker-state",
        allocations_enabled=True,
        profiles={"web": {"image": "seeded-web:1"}},
        relay_urls=["https://relay.test/_browser/endpoint"],
    )
    supervisor = Supervisor(settings, command=docker, probe=lambda url: True)
    refs = supervisor.workspaces("agent-a")["workspaces"]
    payload = {
        "session": hosted.model_copy(update={"workspace_ref": refs[0]["ref"]}).model_dump(),
        "profile": settings.profiles["web"].model_dump(),
        "relay_url": settings.relay_urls[0],
        "endpoint_token": "private-browser-token",
    }
    return supervisor, docker, payload, workspace


def test_opaque_workspace_refs_are_parent_bound_and_symlink_escape_rejected(worker, tmp_path):
    supervisor, _, payload, workspace = worker
    refs = supervisor.workspaces("agent-a")["workspaces"]
    assert len(refs) == 1 and "host" not in refs[0]
    with pytest.raises(BrowserError):
        supervisor.resolve_workspace("agent-b", refs[0]["ref"])
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    (workspace.parent / "escape").symlink_to(outside, target_is_directory=True)
    assert supervisor.workspaces("agent-a")["workspaces"] == refs


def test_profile_manifest_and_relay_cannot_escalate_worker_authority(worker, tmp_path):
    supervisor, docker, payload, workspace = worker
    payload["profile"]["image"] = "unapproved:latest"
    with pytest.raises(BrowserError, match="approved"):
        supervisor.create(payload)
    assert not docker.calls
    payload["profile"] = supervisor.settings.profiles["web"].model_dump()
    payload["relay_url"] = "https://attacker.test/"
    with pytest.raises(BrowserError, match="relay"):
        supervisor.create(payload)
    payload["relay_url"] = supervisor.settings.relay_urls[0]
    (workspace / ".evander/preview.json").write_text(
        '{"command":["node","app"],"docker_options":["--privileged"]}'
    )
    with pytest.raises(ValueError):
        supervisor.create(payload)
    assert not docker.calls


def test_resources_are_durable_bounded_and_firewall_precedes_start(worker, monkeypatch):
    supervisor, docker, payload, workspace = worker
    monkeypatch.setattr("socket.gethostbyname", lambda name: "192.0.2.10")
    result = supervisor.create(payload)
    assert result["state"] == "accepted" and not docker.calls
    recovered = Supervisor(supervisor.settings, command=docker, probe=lambda url: True)
    recovered.reconcile()
    lease = recovered.get(result["id"])
    assert lease["state"] == "running"
    app = next(
        call
        for call in docker.calls
        if call[:2] == ("docker", "run") and call[call.index("--name") + 1].endswith("-app")
    )
    browser = next(
        call
        for call in docker.calls
        if call[:2] == ("docker", "run") and call[call.index("--name") + 1].endswith("-browser")
    )
    assert "--privileged" not in app and "--publish" not in app and "host" not in app
    assert app[app.index("--cap-drop") + 1] == "ALL" and app[app.index("--pull") + 1] == "never"
    assert "/usr/bin/timeout" in app
    pinned = supervisor.root / result["id"] / "workspace"
    assert "type=bind,src=" + str(pinned) + ",dst=/workspace" in app
    pin = next(call for call in docker.calls if "evander_browser_sessions_addon.mount_pin" in call)
    assert pin[:6] == ("nsenter", "--target=1", "--mount", "--root", "--wd", "--")
    assert pin[pin.index("--relative") + 1] == workspace.relative_to(supervisor.settings.worker_root).as_posix()
    assert int(pin[pin.index("--inode") + 1]) == workspace.stat().st_ino
    assert docker.calls.index(pin) < docker.calls.index(app)
    assert "private-browser-token" not in str(app)
    assert "/workspace" not in str(browser)
    assert "--add-host" in browser
    start_index = docker.calls.index(app)
    drop_index = next(
        i for i, call in enumerate(docker.calls) if call[:1] == ("iptables",) and call[-1] == "DROP"
    )
    assert drop_index < start_index
    assert recovered.close(result["id"]) == {"released": True}
    assert not docker.resources and not docker.mounts
    assert "endpoint_token" not in recovered.get(result["id"])


def test_worker_restart_never_replays_interrupted_startup(worker):
    supervisor, docker, payload, workspace = worker
    result = supervisor.create(payload)
    lease = supervisor.get(result["id"])
    lease["state"] = "starting"
    supervisor.save(lease)
    supervisor.reconcile(startup=True)
    assert supervisor.get(result["id"])["state"] == "failed"
    assert not docker.calls


def test_cleanup_failure_remains_visible_and_retryable(worker, monkeypatch):
    supervisor, docker, payload, _ = worker
    monkeypatch.setattr("socket.gethostbyname", lambda name: "192.0.2.10")
    result = supervisor.create(payload)
    supervisor.reconcile()
    docker.fail_remove = True
    with pytest.raises(BrowserError, match="cleanup"):
        supervisor.close(result["id"])
    assert supervisor.get(result["id"])["state"] == "cleanup_failed"
    docker.fail_remove = False
    assert supervisor.close(result["id"])["released"]


def test_worker_cleanup_refuses_foreign_container(worker):
    supervisor, docker, payload, _ = worker
    result = supervisor.create(payload)
    lease = supervisor.get(result["id"])
    docker.resources[lease["name"] + "-app"] = {"Config": {"Labels": {}}}
    with pytest.raises(BrowserError):
        supervisor.close(result["id"])
    assert docker.resources


def test_unconfigured_sql_is_rejected_before_allocating_resources(worker):
    supervisor, docker, payload, workspace = worker
    supervisor.settings.profiles["web"].database_enabled = True
    payload["profile"]["database_enabled"] = True
    (workspace / ".evander/preview.json").write_text(
        '{"command":["node","server.mjs"],"database":"preview"}'
    )
    with pytest.raises(BrowserError, match="database service"):
        supervisor.create(payload)
    assert not supervisor.leases() and not docker.calls


@pytest.mark.parametrize("suffix", ["app", "browser"])
@pytest.mark.parametrize("startup", [False, True])
def test_stopped_preview_process_is_detected_and_can_be_released(worker, monkeypatch, suffix, startup):
    supervisor, docker, payload, _ = worker
    monkeypatch.setattr("socket.gethostbyname", lambda name: "192.0.2.10")
    result = supervisor.create(payload)
    supervisor.reconcile()
    lease = supervisor.get(result["id"])
    assert lease["state"] == "running"
    docker.resources[lease["name"] + "-" + suffix]["State"] = {"Running": False}
    recovered = Supervisor(supervisor.settings, command=docker, probe=lambda url: True)
    recovered.reconcile(startup=startup)
    assert recovered.get(result["id"])["state"] == "failed"
    assert recovered.close(result["id"]) == {"released": True}
    assert not docker.resources


def test_preview_packet_policy_denies_bridge_host_and_undeclared_ports(worker):
    import ipaddress

    supervisor, docker, _, _ = worker
    lease = {
        "name": "evbs-firewall",
        "subnet": "172.30.22.0/24",
        "app_ip": "172.30.22.2",
        "browser_ip": "172.30.22.3",
        "manifest": {"port": 4200, "additional_ports": [5000]},
        "egress": [
            ("172.30.22.2", "172.30.22.1", 1433),
            ("172.30.22.3", "192.0.2.10", 443),
        ],
    }
    supervisor.firewall(lease)
    rules = [call for call in docker.calls if call[0] == "iptables" and "-A" in call]

    def verdict(source, destination, port):
        for rule in rules:
            def option(flag):
                return rule[rule.index(flag) + 1] if flag in rule else None
            if option("--ctstate") and "NEW" not in option("--ctstate").split(","):
                continue
            if option("-s") and ipaddress.ip_address(source) not in ipaddress.ip_network(option("-s")):
                continue
            if option("-d") and ipaddress.ip_address(destination) not in ipaddress.ip_network(option("-d")):
                continue
            if option("--dport") and int(option("--dport")) != port:
                continue
            return option("-j")
        return "RETURN"

    for source in ("172.30.22.2", "172.30.22.3"):
        for port in (22, 443, 8000, 11891):
            assert verdict(source, "172.30.22.1", port) == "DROP"
        assert verdict(source, "172.30.22.20", 4200) == "DROP"
    assert verdict("172.30.22.3", "172.30.22.2", 4200) == "ACCEPT"
    assert verdict("172.30.22.3", "172.30.22.2", 5000) == "ACCEPT"
    assert verdict("172.30.22.3", "172.30.22.2", 22) == "DROP"
    assert verdict("172.30.22.2", "172.30.22.3", 4200) == "DROP"
    assert verdict("172.30.22.2", "172.30.22.1", 1433) == "ACCEPT"
    assert verdict("172.30.22.3", "192.0.2.10", 443) == "ACCEPT"
    assert verdict("172.30.22.2", "192.0.2.10", 443) == "DROP"


@pytest.mark.parametrize("identity", ["1000:1000", "1001:1001"])
def test_preview_identity_is_pinned_and_applied_to_both_containers(worker, monkeypatch, identity):
    supervisor, docker, payload, _ = worker
    supervisor.settings.container_user = identity
    ownership = []
    monkeypatch.setattr("os.chown", lambda path, uid, gid: ownership.append((uid, gid)))
    monkeypatch.setattr("socket.gethostbyname", lambda name: "192.0.2.10")
    result = supervisor.create(payload)
    supervisor.settings.container_user = "2000:2000"
    recovered = Supervisor(supervisor.settings, command=docker, probe=lambda _: True)
    recovered.reconcile()
    containers = [call for call in docker.calls if call[:2] == ("docker", "run")]
    assert len(containers) == 2
    assert all(call[call.index("--user") + 1] == identity for call in containers)
    assert ownership == [tuple(map(int, identity.split(":")))]
    assert recovered.get(result["id"])["state"] == "running"


@pytest.mark.parametrize("identity", ["0:0", "1001:0", "root:root", "-1:1", "1:2:3", "4294967295:1"])
def test_preview_identity_rejects_privileged_or_invalid_ids(worker, identity):
    supervisor, _, _, _ = worker
    with pytest.raises(ValueError):
        WorkerSettings.model_validate({**supervisor.settings.model_dump(), "container_user": identity})


def test_mount_cleanup_failure_keeps_workspace_and_dependency_guard(worker, monkeypatch):
    supervisor, docker, payload, workspace = worker
    monkeypatch.setattr("socket.gethostbyname", lambda _: "192.0.2.10")
    result = supervisor.create(payload)
    supervisor.reconcile()
    docker.fail_unmount = True
    with pytest.raises(BrowserError):
        supervisor.close(result["id"])
    assert supervisor.get(result["id"])["state"] == "cleanup_failed"
    assert (supervisor.root / result["id"]).exists() and (workspace / ".git").exists()
    with supervisor.dependencies.path.open("rb") as database:
        assert database.read(16) == b"SQLite format 3\0"
    import sqlite3
    with sqlite3.connect(supervisor.dependencies.path) as db:
        assert db.execute("SELECT COUNT(*) FROM dependencies").fetchone()[0] == 2
    docker.fail_unmount = False
    assert supervisor.close(result["id"]) == {"released": True}
    assert not docker.mounts and (workspace / ".git").exists()


def test_mount_check_failure_keeps_checkout_and_cleanup_guard(worker, monkeypatch):
    supervisor, docker, payload, workspace = worker
    monkeypatch.setattr("socket.gethostbyname", lambda _: "192.0.2.10")
    result = supervisor.create(payload)
    supervisor.reconcile()
    docker.fail_mount_check = True
    with pytest.raises(BrowserError):
        supervisor.close(result["id"])
    assert supervisor.get(result["id"])["state"] == "cleanup_failed"
    assert (workspace / ".git").exists() and docker.mounts
    docker.fail_mount_check = False
    assert supervisor.close(result["id"]) == {"released": True}
    assert (workspace / ".git").exists() and not docker.mounts


def test_failed_workspace_pin_closes_the_checked_descriptor(worker, monkeypatch):
    import os
    supervisor, docker, payload, _ = worker
    monkeypatch.setattr("socket.gethostbyname", lambda _: "192.0.2.10")
    captured = []
    opened = []
    original_open = os.open
    def tracked_open(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor
    monkeypatch.setattr("os.open", tracked_open)
    def command(*args, **kwargs):
        if "evander_browser_sessions_addon.mount_pin" in args:
            captured.append(opened[-1])
            os.fstat(captured[0])
            raise BrowserError("mount failed")
        return docker(*args, **kwargs)
    supervisor.command = command
    result = supervisor.create(payload)
    supervisor.reconcile()
    assert supervisor.get(result["id"])["state"] == "failed"
    with pytest.raises(OSError):
        os.fstat(captured[0])
    supervisor.close(result["id"])
