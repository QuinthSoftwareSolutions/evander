import subprocess
from pathlib import Path

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
                'export PATH="$HOME/bin:$HOME/toolchains/node-v24.14.0/bin:$PATH"',
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
