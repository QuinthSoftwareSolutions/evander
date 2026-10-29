from pathlib import Path
import json
import runpy
import subprocess
from types import SimpleNamespace
from zipfile import ZipFile

import pytest


ROOT = Path(__file__).resolve().parents[1]
migrate = runpy.run_path(str(ROOT / "scripts/migrate_resource_cleanup.py"))["migrate"]


def wheel(root, name):
    path = root / (name.replace("-", "_") + "-1.0-py3-none-any.whl")
    with ZipFile(path, "w") as archive:
        archive.writestr(
            f"{name}-1.0.dist-info/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n"
        )
    return path


class Host:
    def __init__(self, units):
        self.units = units
        self.calls = []
        self.enabled = {"evander-rabbitmq-reap.timer"}
        self.flags = "--reap --reap-loop"
        self.versions = {}

    def run(self, command, **kwargs):
        self.calls.append(command)
        output = ""
        if command[:2] == ["systemctl", "show"]:
            output = "loaded" if (self.units / command[-1]).exists() else "not-found"
        elif command[-1] == "--help":
            output = self.flags
        elif "-c" in command:
            output = json.dumps(self.versions)
        elif command[:2] == ["systemctl", "disable"]:
            self.enabled.discard(command[-1])
        elif command[:2] == ["systemctl", "enable"]:
            self.enabled.add(command[-1])
        return SimpleNamespace(stdout=output, returncode=0)


@pytest.fixture
def host(tmp_path):
    units = tmp_path / "units"
    units.mkdir()
    (units / "evander-rabbitmq-reap.service").write_text("old unit")
    (units / "evander-rabbitmq-reap.timer").write_text("old timer")
    return Host(units)


def arguments(tmp_path, host):
    return dict(
        addons=["rabbitmq", "sqlserver"],  # SQL Server is not installed.
        wheels=[wheel(tmp_path, "evander"), wheel(tmp_path, "evander-rabbitmq-addon")],
        mode="persistent",
        unit_source=ROOT,
        unit_dir=host.units,
        python="/opt/evander-worker/.venv/bin/python",
        uv="uv",
        run=host.run,
    )


def test_upgrade_is_repeatable_and_installs_before_retiring_timers(tmp_path, host):
    args = arguments(tmp_path, host)
    assert migrate(**args) == ["rabbitmq"]
    install = next(i for i, c in enumerate(host.calls) if c[:3] == ["uv", "pip", "install"])
    disable = next(i for i, c in enumerate(host.calls) if c[:2] == ["systemctl", "disable"])
    assert install < disable
    assert "--no-index" in host.calls[install] and "--no-deps" in host.calls[install]
    assert host.enabled == {"evander-rabbitmq-reap.service"}
    assert "--reap-loop" in (host.units / "evander-rabbitmq-reap.service").read_text()
    assert not (host.units / "evander-rabbitmq-reap.timer").exists()
    assert not (host.units / "evander-sqlserver-reap.service").exists()
    assert migrate(**args) == ["rabbitmq"]
    assert host.enabled == {"evander-rabbitmq-reap.service"}


def test_incompatible_artifact_does_not_stop_old_cleanup(tmp_path, host):
    args = arguments(tmp_path, host)
    host.flags = "--reap"
    with pytest.raises(ValueError, match="Installed cleanup"):
        migrate(**args)
    assert host.enabled == {"evander-rabbitmq-reap.timer"}
    assert not any(c[:2] == ["systemctl", "stop"] for c in host.calls)
    assert (host.units / "evander-rabbitmq-reap.service").read_text() == "old unit"


def test_failed_package_install_leaves_existing_cleanup_running(tmp_path, host):
    args = arguments(tmp_path, host)

    def fail_install(command, **kwargs):
        if command[:3] == ["uv", "pip", "install"]:
            raise subprocess.CalledProcessError(1, command)
        return host.run(command, **kwargs)

    args["run"] = fail_install
    with pytest.raises(subprocess.CalledProcessError):
        migrate(**args)
    assert host.enabled == {"evander-rabbitmq-reap.timer"}
    assert not any(c[:2] == ["systemctl", "stop"] for c in host.calls)


def test_unselected_optional_wheel_is_rejected_before_install(tmp_path, host):
    args = arguments(tmp_path, host)
    args["wheels"].append(wheel(tmp_path, "evander-sqlserver-addon"))
    with pytest.raises(ValueError, match="exactly"):
        migrate(**args)
    assert not any(c[:3] == ["uv", "pip", "install"] for c in host.calls)


def test_reviewed_rollback_restores_timer_and_is_repeatable(tmp_path, host):
    args = arguments(tmp_path, host)
    migrate(**args)
    previous = tmp_path / "previous"
    templates = previous / "packages/rabbitmq-addon/examples/systemd"
    templates.mkdir(parents=True)
    (templates / "evander-rabbitmq-reap.service").write_text(
        "[Service]\nType=oneshot\nExecStart=/opt/evander-worker/.venv/bin/evander-rabbitmq-worker --reap\n"
    )
    (templates / "evander-rabbitmq-reap.timer").write_text("[Timer]\nOnUnitActiveSec=5\n")
    args.update(mode="timer", unit_source=previous)
    for _ in range(2):
        migrate(**args)
        assert host.enabled == {"evander-rabbitmq-reap.timer"}
        assert "Type=oneshot" in (host.units / "evander-rabbitmq-reap.service").read_text()
        assert "OnUnitActiveSec=5" in (host.units / "evander-rabbitmq-reap.timer").read_text()


def installed_arguments(tmp_path, host):
    args = arguments(tmp_path, host)
    source = tmp_path / "reviewed"
    templates = source / "packages/rabbitmq-addon/examples/systemd"
    templates.mkdir(parents=True)
    (source / "pyproject.toml").write_text('[project]\nversion = "9.8.7"\n')
    (source / "packages/rabbitmq-addon/pyproject.toml").write_text('[project]\nversion = "1.2.3"\n')
    (templates / "evander-rabbitmq-reap.service").write_text(
        (
            ROOT / "packages/rabbitmq-addon/examples/systemd/evander-rabbitmq-reap.service"
        ).read_text()
    )
    args.update(packages_installed=True, wheels=None, unit_source=source)
    host.versions = {"evander": "9.8.7", "evander-rabbitmq-addon": "1.2.3"}
    return args


def test_worker_playbook_can_migrate_preinstalled_reviewed_packages(tmp_path, host):
    args = installed_arguments(tmp_path, host)
    assert migrate(**args) == ["rabbitmq"]
    assert host.enabled == {"evander-rabbitmq-reap.service"}
    assert not any(c[:3] == ["uv", "pip", "install"] for c in host.calls)
    assert not (host.units / "evander-rabbitmq-reap.timer").exists()


@pytest.mark.parametrize("package", ["evander", "evander-rabbitmq-addon"])
def test_stale_preinstalled_package_cannot_disable_existing_cleanup(tmp_path, host, package):
    args = installed_arguments(tmp_path, host)
    host.versions[package] = "0.0.1"
    with pytest.raises(ValueError, match="versions do not match"):
        migrate(**args)
    assert host.enabled == {"evander-rabbitmq-reap.timer"}
    assert not any(c[:2] == ["systemctl", "stop"] for c in host.calls)
