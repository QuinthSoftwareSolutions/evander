import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_playwright_bootstrap_rewriter_repairs_profile_without_stripping_foreign_fi(
    tmp_path: Path,
) -> None:
    script = (ROOT / "scripts" / "bootstrap_playwright_vm.sh").read_text(encoding="utf-8")
    marker = "  python3 - \"${target}\" <<'PY'\n"
    rewrite_script = script.split(marker, 1)[1].split("\nPY\n", 1)[0]
    profile = tmp_path / ".profile"
    profile.write_text(
        "\n".join(
            [
                'export AGENT_PROVIDER="${AGENT_PROVIDER:-claude}"',
                'if [ -f "$HOME/.claude/settings.json" ]; then',
                '  export ANTHROPIC_BASE_URL="$(python3 -c \'print("relay")\')"',
                'export PATH="$HOME/bin:$HOME/toolchains/node-v24.22.1/bin:$PATH"',
                'export EVANDER_PLAYWRIGHT_RUNTIME="$HOME/playwright-runtime/node_modules"',
                'if [ -z "${PLAYWRIGHT_MCP_EXECUTABLE_PATH:-}" ] && command -v node >/dev/null 2>&1; then',
                '  if _evander_playwright_chromium="$(node -e \'process.stdout.write("chromium")\' 2>/dev/null)" && [ -n "$_evander_playwright_chromium" ]; then',
                '    export PLAYWRIGHT_MCP_EXECUTABLE_PATH="$_evander_playwright_chromium"',
                "  fi",
                "  unset _evander_playwright_chromium",
                "fi",
                "if true; then",
                '  export KEEP_THIS_BLOCK="1"',
                "fi",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    subprocess.run(
        ["python3", "-", str(profile)],
        input=rewrite_script,
        text=True,
        check=True,
    )
    rewritten = profile.read_text(encoding="utf-8")

    subprocess.run(["bash", "-n", str(profile)], check=True)
    assert 'if [ -f "$HOME/.claude/settings.json" ]; then\n' in rewritten
    assert 'export ANTHROPIC_BASE_URL="$(python3 -c \'print("relay")\')"\nfi\n' in rewritten
    assert 'export KEEP_THIS_BLOCK="1"\nfi\n' in rewritten
    assert (
        rewritten.count(
            'if [ -z "${PLAYWRIGHT_MCP_EXECUTABLE_PATH:-}" ] && command -v node >/dev/null 2>&1; then'
        )
        == 1
    )


@pytest.mark.parametrize("bootstrap", ["bootstrap_playwright_vm.sh", "bootstrap_frontend_deps_vm.sh"])
def test_node_bootstrap_upgrades_both_startup_files_idempotently(tmp_path, bootstrap):
    script = (ROOT / "scripts" / bootstrap).read_text(encoding="utf-8")
    start = script.index("append_node_path_setup() {")
    end = script.index("\nexport PATH=", start)
    rewrite = script[start:end]
    legacy = 'export PATH="$HOME/bin:$HOME/toolchains/node-v24.14.0/bin:$PATH"'
    current = 'export PATH="$HOME/bin:$HOME/toolchains/node-v24.22.1/bin:$PATH"'
    foreign = 'export PATH="$HOME/custom/bin:$PATH"'
    for name in (".profile", ".bashrc"):
        (tmp_path / name).write_text("\n".join([legacy, current, foreign]) + "\n")
    for version in ("24.14.0", "24.22.1"):
        node = tmp_path / "toolchains" / f"node-v{version}" / "bin" / "node"
        node.parent.mkdir(parents=True)
        node.write_text(f"#!/bin/sh\nprintf '%s\\n' 'v{version}'\n")
        node.chmod(0o755)
    env = {**os.environ, "HOME": str(tmp_path), "PATH": "/usr/bin:/bin",
           "PLAYWRIGHT_MCP_EXECUTABLE_PATH": "/seeded/chromium"}
    for _ in range(2):
        subprocess.run(["bash", "--noprofile", "--norc", "-c", rewrite], env=env,
                       check=True, timeout=10)
    for name in (".profile", ".bashrc"):
        rewritten = (tmp_path / name).read_text()
        assert "node-v24.14.0" not in rewritten
        assert rewritten.count(current) == 1
        assert rewritten.count(foreign) == 1
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c",
         '. "$HOME/.profile"; . "$HOME/.bashrc"; node --version'],
        env=env, check=True, capture_output=True, text=True, timeout=10,
    )
    assert result.stdout.strip() == "v24.22.1"
