from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "scripts" / "verify_seeded_playwright_runtime.mjs"


def _write_fake_playwright_runtime(
    tmp_path: Path,
    *,
    expected_build: str,
) -> tuple[Path, Path]:
    runtime = tmp_path / "node_modules"
    package = runtime / "playwright"
    package.mkdir(parents=True)
    package.joinpath("index.js").write_text(
        (
            "const path = require('node:path');\n"
            "exports.chromium = {\n"
            "  executablePath() {\n"
            f"    return path.join(process.env.PLAYWRIGHT_BROWSERS_PATH, "
            f"'chromium-{expected_build}', 'chrome-linux', 'chrome');\n"
            "  },\n"
            "};\n"
        ),
        encoding="utf-8",
    )
    browsers = tmp_path / "ms-playwright"
    browsers.mkdir()
    return runtime, browsers


def _run_verifier(runtime: Path, browsers: Path) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the Playwright seed verifier")
    return subprocess.run(
        [node, str(VERIFIER), str(runtime), str(browsers)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_playwright_seed_verifier_accepts_matching_executable(tmp_path: Path) -> None:
    runtime, browsers = _write_fake_playwright_runtime(tmp_path, expected_build="1234")
    executable = browsers / "chromium-1234" / "chrome-linux" / "chrome"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    result = _run_verifier(runtime, browsers)

    assert result.returncode == 0
    assert result.stdout.strip() == str(executable)
    assert result.stderr == ""


def test_playwright_seed_verifier_reports_mismatched_builds_actionably(
    tmp_path: Path,
) -> None:
    runtime, browsers = _write_fake_playwright_runtime(tmp_path, expected_build="1234")
    available = browsers / "chromium-1228" / "chrome-linux" / "chrome"
    available.parent.mkdir(parents=True)
    available.write_text("#!/bin/sh\n", encoding="utf-8")
    available.chmod(0o755)

    result = _run_verifier(runtime, browsers)

    assert result.returncode == 4
    assert "Seeded Playwright runtime/browser mismatch" in result.stderr
    assert "chromium-1234" in result.stderr
    assert "Available Chromium cache entries: chromium-1228" in result.stderr
    assert "same Playwright installation" in result.stderr
    assert "downloads are disabled" in result.stderr
