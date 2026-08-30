from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.config import GatewayConfig
from evander.effects.providers.agent_session import (
    PLAYWRIGHT_CONTAINER_PROMPT_MARKER,
    build_container_session_files,
    container_codex_turn_spec,
    container_prompt_command,
    container_prompt_with_session_capabilities,
)


def _playwright_session_files() -> list[dict[str, object]]:
    return build_container_session_files(
        config=GatewayConfig(),
        session_id="session-playwright",
        model="gpt-5.4",
        dotnet=False,
        nuget=False,
        playwright=True,
    )


def test_playwright_launch_helper_uses_seed_with_repo_chromium_object(
    tmp_path: Path,
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the Playwright launch helper")
    files = _playwright_session_files()
    helper_file = next(
        entry
        for entry in files
        if entry["destination_path"] == "/home/agent/.evander/playwright-launch.mjs"
    )
    helper_path = tmp_path / "playwright-launch.mjs"
    helper_content = str(helper_file["content"])
    helper_path.write_text(helper_content, encoding="utf-8")
    assert 'from "playwright"' not in helper_content
    assert "require(" not in helper_content

    env = {
        **os.environ,
        "PLAYWRIGHT_MCP_EXECUTABLE_PATH": "/opt/evander/ms-playwright/chromium-1217/chrome",
    }
    result = subprocess.run(
        [
            node,
            "-e",
            (
                "const helper = await import(process.argv[1]);"
                "const chromium = { launch: async (options) => "
                "process.stdout.write(JSON.stringify(options)) };"
                "await helper.launchSeededChromium(chromium, "
                "{ headless: false, executablePath: '/wrong/revision/chrome' });"
            ),
            helper_path.as_uri(),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "headless": False,
        "executablePath": "/opt/evander/ms-playwright/chromium-1217/chrome",
    }


def test_playwright_session_prompt_explains_repo_local_launch_contract() -> None:
    session = SimpleNamespace(metadata={"playwright": True}, codex_auth_mode=None)
    prompt = container_prompt_with_session_capabilities("Run the UI tests.", session)
    assert prompt.count(PLAYWRIGHT_CONTAINER_PROMPT_MARKER) == 1
    assert prompt == container_prompt_with_session_capabilities(prompt, session)
    assert "Repository-local Playwright" in prompt
    assert "EVANDER_PLAYWRIGHT_LAUNCH_HELPER" in prompt
    assert "PLAYWRIGHT_MCP_EXECUTABLE_PATH" in prompt
    assert "Do not hard-code a cache revision" in prompt

    command = container_prompt_command(
        prompt="Run the UI tests.",
        model="gpt-5.4",
        resume_thread_id=None,
        session=session,
    )
    turn = container_codex_turn_spec(
        prompt="Run the UI tests.",
        model="gpt-5.4",
        resume_thread_id=None,
        session=session,
        config=GatewayConfig(),
    )

    assert PLAYWRIGHT_CONTAINER_PROMPT_MARKER in command
    assert turn is not None
    assert PLAYWRIGHT_CONTAINER_PROMPT_MARKER in turn.prompt


def test_playwright_launch_helper_applies_session_browser_proxy(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the Playwright launch helper")
    helper_file = next(
        entry
        for entry in _playwright_session_files()
        if entry["destination_path"] == "/home/agent/.evander/playwright-launch.mjs"
    )
    helper_path = tmp_path / "playwright-launch.mjs"
    helper_path.write_text(str(helper_file["content"]), encoding="utf-8")
    env = {
        **os.environ,
        "PLAYWRIGHT_MCP_EXECUTABLE_PATH": "/opt/chromium",
        "EVANDER_HTTP_TARGET_BROWSER_PROXY_URL": "http://control-plane.test:11778",
        "EVANDER_SESSION_ID": "session-playwright",
        "EVANDER_MCP_SESSION_TOKEN": "session-token",
    }
    result = subprocess.run(
        [
            node,
            "-e",
            (
                "const helper = await import(process.argv[1]);"
                "process.stdout.write(JSON.stringify(helper.seededChromiumLaunchOptions({ headless: true })));"
            ),
            helper_path.as_uri(),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["proxy"] == {
        "server": "http://control-plane.test:11778",
        "username": "session-playwright",
        "password": "session-token",
    }


def test_playwright_launch_helper_rejects_conflicting_proxy(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the Playwright launch helper")
    helper_file = next(
        entry
        for entry in _playwright_session_files()
        if entry["destination_path"] == "/home/agent/.evander/playwright-launch.mjs"
    )
    helper_path = tmp_path / "playwright-launch.mjs"
    helper_path.write_text(str(helper_file["content"]), encoding="utf-8")
    env = {
        **os.environ,
        "PLAYWRIGHT_MCP_EXECUTABLE_PATH": "/opt/chromium",
        "EVANDER_HTTP_TARGET_BROWSER_PROXY_URL": "http://control-plane.test:11778",
        "EVANDER_SESSION_ID": "session-playwright",
        "EVANDER_MCP_SESSION_TOKEN": "session-token",
    }
    result = subprocess.run(
        [
            node,
            "-e",
            (
                "const helper = await import(process.argv[1]);"
                "helper.seededChromiumLaunchOptions({ proxy: { server: 'http://other.test' } });"
            ),
            helper_path.as_uri(),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "cannot replace the Evander session proxy" in result.stderr


def test_non_playwright_session_does_not_receive_launch_guidance_or_helper() -> None:
    session = SimpleNamespace(metadata={"playwright": False}, codex_auth_mode=None)
    command = container_prompt_command(
        prompt="Continue.",
        model="gpt-5.4",
        resume_thread_id=None,
        session=session,
    )
    files = build_container_session_files(
        config=GatewayConfig(),
        session_id="session-standard",
        model="gpt-5.4",
        dotnet=False,
        nuget=False,
        playwright=False,
    )

    assert PLAYWRIGHT_CONTAINER_PROMPT_MARKER not in command
    assert all(
        entry["destination_path"] != "/home/agent/.evander/playwright-launch.mjs" for entry in files
    )
