import os
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
CLIENT_ARCHIVE_SCRIPT = ROOT / "scripts" / "build_mssql_client_toolchain_archive.sh"


def test_mssql_client_toolchain_archive_is_self_contained(tmp_path: Path) -> None:
    tools_root = tmp_path / "mssql-tools18"
    odbc_root = tmp_path / "msodbcsql18"
    runtime_root = tmp_path / "runtime"
    sqlcmd = tools_root / "bin" / "sqlcmd"
    driver = odbc_root / "lib64" / "libmsodbcsql-18.test.so"
    runtime_libs = [runtime_root / name for name in ("libodbc.so.2", "libodbcinst.so.2")]
    archive = tmp_path / "mssql-client.tar.gz"

    sqlcmd.parent.mkdir(parents=True)
    driver.parent.mkdir(parents=True)
    runtime_root.mkdir()
    sqlcmd.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    sqlcmd.chmod(0o755)
    driver.write_bytes(b"driver")
    for runtime_lib in runtime_libs:
        runtime_lib.write_bytes(runtime_lib.name.encode("utf-8"))

    result = subprocess.run(
        [str(CLIENT_ARCHIVE_SCRIPT), "--output", str(archive)],
        cwd=ROOT,
        env={
            **os.environ,
            "HOST_MSSQL_TOOLS_ROOT": str(tools_root),
            "HOST_MSSQL_ODBC_ROOT": str(odbc_root),
            "HOST_MSSQL_RUNTIME_LIBS": " ".join(str(path) for path in runtime_libs),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    with tarfile.open(archive, "r:gz") as payload:
        names = {name.removeprefix("./") for name in payload.getnames()}
        assert "bin/sqlcmd" in names
        assert "lib/libodbc.so.2" in names
        assert "lib/libodbcinst.so.2" in names
        assert "msodbcsql18/lib64/libmsodbcsql-18.test.so" in names
        env_text = payload.extractfile("./env.sh").read().decode("utf-8")
        odbcinst_text = payload.extractfile("./etc/odbcinst.ini").read().decode("utf-8")

    assert "/opt/evander/toolchains/mssql-client/bin" in env_text
    assert "ODBCSYSINI" in env_text
    assert (
        "Driver=/opt/evander/toolchains/mssql-client/"
        "msodbcsql18/lib64/libmsodbcsql-18.test.so"
    ) in odbcinst_text
