"""Exercise the deployment builder through its CLI without Docker or npm access."""

import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


PACKAGE = Path(__file__).resolve().parents[1]


@pytest.fixture
def image_builder(tmp_path):
    package = tmp_path / "checkout/packages/codex-security-addon"
    for name in [
        "scripts/build-worker-image.py",
        "scripts/build-runtime.sh",
        "examples/Dockerfile",
        "package.json",
        "package-lock.json",
        "evander_codex_security_addon/runner.py",
        "evander_codex_security_addon/codex_bridge.py",
        "evander_codex_security_addon/configure.mjs",
    ]:
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PACKAGE / name, target)
    base = tmp_path / "checkout/dist/evander-runner.tar"
    base.parent.mkdir()
    base.write_bytes(b"prepared base image")
    tools = tmp_path / "tools"
    tools.mkdir()
    node = tools / "node"
    node.write_bytes(b"pinned Node executable")
    log = tmp_path / "commands.jsonl"
    snapshot = tmp_path / "staged-runtime.json"
    for name, script in {
        "npm": """
import json, os, pathlib, sys
with open(os.environ['BUILD_LOG'], 'a') as out:
    out.write(json.dumps(['npm', *sys.argv[1:]]) + '\\n')
root = pathlib.Path(sys.argv[sys.argv.index('--prefix') + 1]) / 'node_modules'
root.mkdir(exist_ok=True)
(root / 'locked-package').write_text('pinned runtime')
(root / 'locked-package').chmod(0o666)
root.chmod(0o777)
""",
        "docker": """
import json, os, pathlib, stat, sys, time
with open(os.environ['BUILD_LOG'], 'a') as out:
    out.write(json.dumps(['docker', *sys.argv[1:]]) + '\\n')
if sys.argv[1] == 'build':
    context = pathlib.Path(sys.argv[-1])
    runtime = context / 'runtime'
    data = {str(p.relative_to(runtime)): p.read_text() for p in runtime.rglob('*') if p.is_file()}
    data['modes'] = {str(p.relative_to(runtime)): stat.S_IMODE(p.stat().st_mode) for p in [runtime, *runtime.rglob('*')]}
    data['context_path'] = str(context)
    data['Dockerfile'] = (context / 'Dockerfile').read_text()
    pathlib.Path(os.environ['BUILD_SNAPSHOT']).write_text(json.dumps(data))
if sys.argv[1] == 'save':
    context = pathlib.Path(json.loads(pathlib.Path(os.environ['BUILD_SNAPSHOT']).read_text())['context_path'])
    if context.exists():
        raise SystemExit('Build context still occupies disk during export')
    if os.environ.get('STALLED_SAVE'):
        time.sleep(60)
    payload = b'' if os.environ.get('EMPTY_SAVE') else b'scanner image archive'
    if '-o' in sys.argv:
        pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_bytes(payload)
    else:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
    if os.environ.get('FAIL_SAVE'):
        raise SystemExit(42)
""",
    }.items():
        target = tools / name
        target.write_text(f"#!{sys.executable}\n" + script)
        target.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tools}:{os.environ['PATH']}",
        "DOCKER": str(tools / "docker"),
        "HOST_NODE_BINARY": str(node),
        "BUILD_LOG": str(log),
        "BUILD_SNAPSHOT": str(snapshot),
        "RUNNER_FORCE_REBUILD": "false",
    }
    for name in list(env):
        if name.startswith("SECURITY_SCAN_"):
            del env[name]
    return package, env, log, snapshot


def run_builder(fixture, *args, **env):
    package, environment, _, _ = fixture
    return subprocess.run(
        [sys.executable, str(package / "scripts/build-worker-image.py"), *args],
        env={**environment, **env},
        capture_output=True,
        text=True,
        timeout=20,
    )


def calls(fixture):
    log = fixture[2]
    return [json.loads(line)[:2] for line in log.read_text().splitlines()] if log.exists() else []


def test_unchanged_scan_image_reuses_archive_without_install_or_docker(image_builder):
    first = run_builder(image_builder)
    assert first.returncode == 0, first.stderr
    archive = image_builder[0].parents[1] / "dist/evander-security-scan.tar"
    manifest = json.loads(Path(str(archive) + ".manifest").read_text())
    assert manifest["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    staged = json.loads(image_builder[3].read_text())
    assert staged["runner.py"] == (PACKAGE / "evander_codex_security_addon/runner.py").read_text()
    assert staged["node_modules/locked-package"] == "pinned runtime"
    assert not (image_builder[0] / "node_modules").exists()
    assert all(mode & 0o022 == 0 for mode in staged["modes"].values())
    assert staged["modes"]["node"] == staged["modes"]["codex"] == 0o755
    assert "python3 /opt/evander/codex-security/codex_bridge.py" in staged["codex"]
    assert calls(image_builder) == [
        ["npm", "ci"],
        ["docker", "load"],
        ["docker", "build"],
        ["docker", "save"],
    ]
    second = run_builder(image_builder, DOCKER="/missing/docker")
    assert second.returncode == 0, second.stderr
    assert "Reused cached" in second.stdout
    assert len(calls(image_builder)) == 4
    (image_builder[0].parents[1] / "unrelated-change.txt").write_text("no image impact")
    assert "Reused cached" in run_builder(image_builder).stdout


@pytest.mark.parametrize(
    "changed", ["base", "node", "runner", "bridge", "configure", "lock", "builder", "dockerfile"]
)
def test_changed_runtime_or_base_rebuilds(image_builder, changed):
    assert run_builder(image_builder).returncode == 0
    package = image_builder[0]
    path = {
        "base": package.parents[1] / "dist/evander-runner.tar",
        "node": Path(image_builder[1]["HOST_NODE_BINARY"]),
        "runner": package / "evander_codex_security_addon/runner.py",
        "bridge": package / "evander_codex_security_addon/codex_bridge.py",
        "configure": package / "evander_codex_security_addon/configure.mjs",
        "lock": package / "package-lock.json",
        "builder": package / "scripts/build-runtime.sh",
        "dockerfile": package / "examples/Dockerfile",
    }[changed]
    # Whitespace is valid for all these inputs and changes their content identity.
    with path.open("ab") as output:
        output.write(b"\n")
    rebuilt = run_builder(image_builder)
    assert rebuilt.returncode == 0, rebuilt.stderr
    assert len(calls(image_builder)) == 8


@pytest.mark.parametrize("damage", ["archive", "manifest", "missing", "force"])
def test_damaged_or_forced_cache_is_rebuilt(image_builder, damage):
    assert run_builder(image_builder).returncode == 0
    archive = image_builder[0].parents[1] / "dist/evander-security-scan.tar"
    if damage == "archive":
        archive.write_bytes(b"corrupt")
    elif damage == "manifest":
        Path(str(archive) + ".manifest").write_text("{}")
    elif damage == "missing":
        archive.unlink()
    result = run_builder(image_builder, *(["--force"] if damage == "force" else []))
    assert result.returncode == 0, result.stderr
    assert archive.read_bytes() == b"scanner image archive"
    assert len(calls(image_builder)) == 8


@pytest.mark.parametrize("suffix", [".tar", ".tar.gz"])
@pytest.mark.parametrize("failure", ["FAIL_SAVE", "EMPTY_SAVE"])
def test_failed_save_preserves_previous_verified_cache(image_builder, suffix, failure):
    archive = image_builder[0].parents[1] / ("dist/evander-security-scan" + suffix)
    env = {"SECURITY_SCAN_ARCHIVE_PATH": str(archive)}
    assert run_builder(image_builder, **env).returncode == 0
    manifest = Path(str(archive) + ".manifest")
    before = archive.read_bytes(), manifest.read_bytes()
    failed = run_builder(image_builder, "--force", **env, **{failure: "1"})
    assert failed.returncode != 0
    assert (archive.read_bytes(), manifest.read_bytes()) == before
    assert not list(archive.parent.glob(".scanner-archive-*"))
    assert "Reused cached" in run_builder(image_builder, **env).stdout


def test_compressed_export_streams_and_reuses_verified_archive(image_builder):
    archive = image_builder[0].parents[1] / "dist/evander-security-scan.tar.gz"
    env = {"SECURITY_SCAN_ARCHIVE_PATH": str(archive)}
    first = run_builder(image_builder, **env)
    assert first.returncode == 0, first.stderr
    assert gzip.decompress(archive.read_bytes()) == b"scanner image archive"
    manifest = json.loads(Path(str(archive) + ".manifest").read_text())
    assert manifest["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert manifest["inputs"]["archive_format"] == "tar.gz"
    last_command = json.loads(image_builder[2].read_text().splitlines()[-1])
    assert last_command == ["docker", "save", "evander-security-scan:latest"]
    assert "Reused cached" in run_builder(image_builder, DOCKER="/missing/docker", **env).stdout
    assert len(calls(image_builder)) == 4


def test_compressor_failure_stops_export_and_preserves_cache(image_builder):
    archive = image_builder[0].parents[1] / "dist/evander-security-scan.tar.gz"
    env = {"SECURITY_SCAN_ARCHIVE_PATH": str(archive)}
    assert run_builder(image_builder, **env).returncode == 0
    manifest = Path(str(archive) + ".manifest")
    before = archive.read_bytes(), manifest.read_bytes()
    compressor = Path(image_builder[1]["DOCKER"]).with_name("gzip")
    compressor.write_text("#!/bin/sh\nexit 19\n")
    compressor.chmod(0o755)
    failed = run_builder(image_builder, "--force", STALLED_SAVE="1", **env)
    assert failed.returncode != 0
    assert (archive.read_bytes(), manifest.read_bytes()) == before
    assert not list(archive.parent.glob(".scanner-archive-*"))


def test_portable_cache_key_does_not_install_or_build(image_builder, tmp_path):
    first = run_builder(image_builder, "--cache-key", DOCKER="/missing/docker")
    assert first.returncode == 0, first.stderr
    assert len(first.stdout.strip()) == 64
    assert calls(image_builder) == []
    copied = tmp_path / "another-agent"
    shutil.copytree(image_builder[0].parents[1], copied)
    fixture = (copied / "packages/codex-security-addon", *image_builder[1:])
    second = run_builder(fixture, "--cache-key")
    assert second.stdout == first.stdout
    assert calls(image_builder) == []


def test_operator_policy_is_staged_and_invalidates_cache(image_builder, tmp_path):
    policy = tmp_path / "operator-configure.mjs"
    policy.write_text("// operator isolation policy\n")
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        (PACKAGE / "examples/Dockerfile").read_text() + "\nENV OPERATOR_POLICY=enabled\n"
    )
    env = {"SECURITY_SCAN_CONFIGURE_PATH": str(policy), "SECURITY_SCAN_DOCKERFILE": str(dockerfile)}
    assert run_builder(image_builder, **env).returncode == 0
    snapshot = json.loads(image_builder[3].read_text())
    assert snapshot["configure.mjs"] == policy.read_text()
    assert (
        snapshot["configure-stock.mjs"]
        == (PACKAGE / "evander_codex_security_addon/configure.mjs").read_text()
    )
    assert "OPERATOR_POLICY=enabled" in snapshot["Dockerfile"]
    policy.write_text("// revised operator isolation policy\n")
    assert run_builder(image_builder, **env).returncode == 0
    assert len(calls(image_builder)) == 8


def test_missing_base_fails_before_install_or_build(image_builder):
    result = run_builder(
        image_builder, "--cache-key", SECURITY_SCAN_BASE_ARCHIVE="/missing/base.tar"
    )
    assert result.returncode != 0
    assert "Missing or unreadable" in result.stderr
    assert calls(image_builder) == []
