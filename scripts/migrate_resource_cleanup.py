#!/usr/bin/env python3
"""CI/CD migration for explicitly installed resource cleanup services.

Run only with reviewed wheels and units. The timer mode restores units and wheels
from a previous reviewed release; it is not an automatic production rollback.
"""

import argparse
from configparser import ConfigParser
from email.parser import BytesParser
import os
from pathlib import Path
import re
import subprocess
import tempfile
from zipfile import ZipFile


ADDONS = ("rabbitmq", "sqlserver", "test-fixtures")


def _write_unit(path, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content.encode())
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _wheel_name(path):
    with ZipFile(path) as wheel:
        metadata = [name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("Expected exactly one wheel metadata record")
        name = BytesParser().parsebytes(wheel.read(metadata[0])).get("Name", "")
    return re.sub(r"[-_.]+", "-", name).lower()


def migrate(*, addons, wheels, mode, unit_source, unit_dir, python, uv, run=subprocess.run):
    """Install compatible artifacts before changing any existing service schedule."""
    addons = list(dict.fromkeys(addons))
    if not addons or any(addon not in ADDONS for addon in addons):
        raise ValueError("Select only supported resource add-ons explicitly")

    def command(*args):
        return run(list(map(str, args)), check=True, capture_output=True, text=True)

    def installed(unit):
        return (
            command("systemctl", "show", "--property=LoadState", "--value", unit).stdout.strip()
            == "loaded"
        )

    selected = [addon for addon in addons if installed(f"evander-{addon}-reap.service")]
    if not selected:
        return []
    expected_wheels = {"evander", *(f"evander-{addon}-addon" for addon in selected)}
    actual_wheels = [_wheel_name(path) for path in wheels]
    if set(actual_wheels) != expected_wheels or len(actual_wheels) != len(expected_wheels):
        raise ValueError(
            "Provide exactly the core wheel and one wheel per installed selected add-on"
        )

    units = {}
    timers = {}
    for addon in selected:
        base = f"evander-{addon}-reap"
        source = Path(unit_source) / "packages" / f"{addon}-addon" / "examples" / "systemd"
        content = (source / f"{base}.service").read_text()
        config = ConfigParser(interpolation=None)
        config.read_string(content)
        wanted_type, wanted_flag = (
            ("simple", "--reap-loop") if mode == "persistent" else ("oneshot", "--reap")
        )
        if (
            config["Service"]["Type"] != wanted_type
            or wanted_flag not in config["Service"]["ExecStart"].split()
        ):
            raise ValueError("Unit source does not match the requested cleanup mode")
        # Keep unit commands aligned with the artifact installation environment.
        content = content.replace("/opt/evander-worker/.venv/bin/", str(Path(python).parent) + "/")
        units[base] = content
        if mode == "timer":
            timers[base] = (source / f"{base}.timer").read_text()

    command(
        uv, "pip", "install", "--python", python, "--no-index", "--no-deps", "--reinstall", *wheels
    )
    for addon in selected:
        help_text = command(Path(python).parent / f"evander-{addon}-worker", "--help").stdout
        flag = "--reap-loop" if mode == "persistent" else "--reap"
        if flag not in help_text.split():
            raise ValueError("Installed cleanup command does not support the requested mode")

    for base, content in units.items():
        if installed(f"{base}.timer"):
            command("systemctl", "disable", "--now", f"{base}.timer")
        command("systemctl", "stop", f"{base}.service")
        if mode == "timer":
            command("systemctl", "disable", f"{base}.service")
        _write_unit(Path(unit_dir) / f"{base}.service", content)
        timer = Path(unit_dir) / f"{base}.timer"
        if mode == "persistent":
            timer.unlink(missing_ok=True)
        else:
            _write_unit(timer, timers[base])
    command("systemctl", "daemon-reload")
    for base in units:
        suffix = "service" if mode == "persistent" else "timer"
        command("systemctl", "enable", "--now", f"{base}.{suffix}")
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--addon", action="append", choices=ADDONS, required=True)
    parser.add_argument("--wheel", action="append", type=Path, required=True)
    parser.add_argument("--mode", choices=("persistent", "timer"), default="persistent")
    parser.add_argument("--unit-source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--unit-dir", type=Path, default=Path("/etc/systemd/system"))
    parser.add_argument("--python", default="/opt/evander-worker/.venv/bin/python")
    parser.add_argument("--uv", default="uv")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run this deployment migration as root through the approved CI/CD job")
    selected = migrate(
        addons=args.addon,
        wheels=args.wheel,
        mode=args.mode,
        unit_source=args.unit_source,
        unit_dir=args.unit_dir,
        python=args.python,
        uv=args.uv,
    )
    print("Migrated cleanup services: " + (", ".join(selected) or "none installed"))


if __name__ == "__main__":
    main()
