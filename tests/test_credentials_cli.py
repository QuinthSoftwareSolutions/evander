import json

from typer.testing import CliRunner

from evander.cli import app


def test_credentials_cli_initialization_status_and_maintenance_guard(tmp_path):
    runner = CliRunner()
    runtime = tmp_path / "runtime"
    key = tmp_path / "keys" / "master.key"
    options = ["--runtime-dir", str(runtime), "--key-file", str(key)]
    status = runner.invoke(app, ["credentials", "status", *options])
    assert status.exit_code == 0
    assert not json.loads(status.stdout)["store"]["initialized"]
    assert not runtime.exists()
    initialized = runner.invoke(app, ["credentials", "init", *options])
    assert initialized.exit_code == 0, initialized.stdout
    assert json.loads(initialized.stdout) == {"initialized": True}
    assert len(key.read_bytes()) == 32 and key.stat().st_mode & 0o777 == 0o600
    status = runner.invoke(app, ["credentials", "status", *options])
    assert json.loads(status.stdout)["store"]["available"]
    before = (runtime / "credentials" / "credentials.db").read_bytes()
    rotation = runner.invoke(
        app, ["credentials", "rotate-key", *options, "--new-key-file", str(tmp_path / "next.key")]
    )
    assert rotation.exit_code == 1
    assert (runtime / "credentials" / "credentials.db").read_bytes() == before
    migration = runner.invoke(
        app, ["credentials", "migrate", *options, "--family", "teams", "--apply"]
    )
    assert migration.exit_code == 1
    assert not (runtime / "host-config.json").exists()
