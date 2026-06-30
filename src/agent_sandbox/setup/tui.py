from __future__ import annotations

from dataclasses import dataclass
import getpass
import json
import os
from pathlib import Path
import select
import sys

import click
import typer

from agent_sandbox.setup.engine import (
    DEFAULT_ADMIN_AUTH_MODE,
    DEFAULT_ADMIN_HOST,
    DEFAULT_ADMIN_PORT,
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_RUNTIME_DIR,
    PROVIDER_ENV_VARS,
    SetupManager,
    SetupPreflightError,
)


@dataclass(frozen=True)
class _MenuOption:
    value: str
    label: str
    description: str = ""


@dataclass(frozen=True)
class _ChecklistAction:
    kind: str
    selected_index: int
    item_id: str = ""


_CHECKLIST_HOTKEYS = {
    "q": "quit",
    "p": "plan",
    "d": "doctor",
    "l": "launch",
}

_ADMIN_AUTH_OPTIONS = (
    _MenuOption("local_password", "Local password (recommended)"),
    _MenuOption("none", "No authentication (discouraged)"),
)

_EXECUTION_OPTIONS = (
    _MenuOption("container_same_host", "Containers on this host"),
    _MenuOption("container_worker_host", "Containers on a separate worker host"),
    _MenuOption("libvirt_vm", "VM-backed agents"),
)

_PROVIDER_OPTIONS = (
    _MenuOption("openai", "OpenAI"),
    _MenuOption("anthropic", "Anthropic"),
    _MenuOption("ollama", "Ollama"),
)


def run_setup_tui(*, project_dir: Path, runtime_dir: Path = DEFAULT_RUNTIME_DIR) -> None:
    manager = SetupManager(project_dir=project_dir, runtime_dir=runtime_dir)
    state = manager.load_state()
    interactive = _is_interactive_terminal()
    if not state.secret_storage_acknowledged:
        typer.echo("Evander keeps provider credentials on the control-plane host.")
        typer.echo(f"Secrets entered during setup will be stored in: {state.secret_file}")
        typer.echo("Secret values are not written to agents, workflow files, prompts, or setup state.")
        if typer.confirm("Continue with this host secret file?", default=True):
            manager.acknowledge_secret_storage(state)
            state = manager.load_state()
        else:
            raise typer.Exit(1)

    selected_index = 0
    while True:
        if interactive:
            action = _prompt_checklist_action(manager, state, selected_index=selected_index)
            selected_index = action.selected_index
            _clear_screen()
            if action.kind == "quit":
                return
            if action.kind == "plan":
                typer.echo(json.dumps(manager.plan(), indent=2, sort_keys=True))
                continue
            if action.kind == "doctor":
                typer.echo(json.dumps(manager.doctor(), indent=2, sort_keys=True))
                continue
            if action.kind == "launch":
                state = _launch(manager, state)
                continue
            state = _configure_item(manager, state, action.item_id, interactive=interactive)
            continue

        _clear_screen()
        _render_checklist(manager, state)
        choice = typer.prompt(
            "Select item number, L to launch when ready, P for plan, D for doctor, Q to quit",
            default="q",
        ).strip().lower()
        _clear_screen()
        if choice in {"q", "quit"}:
            return
        if choice == "p":
            typer.echo(json.dumps(manager.plan(), indent=2, sort_keys=True))
            continue
        if choice == "d":
            typer.echo(json.dumps(manager.doctor(), indent=2, sort_keys=True))
            continue
        if choice == "l":
            state = _launch(manager, state)
            continue
        try:
            index = int(choice) - 1
            item = manager.checklist(state)[index]
        except (ValueError, IndexError):
            typer.echo("Unknown selection.")
            continue
        state = _configure_item(manager, state, item.id, interactive=interactive)


def _render_checklist(
    manager: SetupManager,
    state,
    *,
    selected_index: int | None = None,
) -> None:
    typer.echo("")
    typer.echo("Evander Setup")
    typer.echo("=" * 13)
    items = manager.checklist(state)
    for index, item in enumerate(items):
        marker = "completed" if item.status == "completed" else item.status
        if selected_index is None:
            typer.echo(f"{index + 1}. [{marker}] {item.label}: {item.summary}")
            continue
        cursor = ">" if index == selected_index else " "
        line = f"{cursor} [{marker}] {item.label}: {item.summary}"
        if index == selected_index:
            line = click.style(line, reverse=True)
        typer.echo(line)
    launch_status = "available" if manager.required_ready(state) else "blocked"
    if selected_index is None:
        typer.echo(f"\nLaunch: {launch_status}")
        return
    typer.echo("")
    typer.echo("Up/Down move, Enter opens selected.")
    typer.echo(f"Hotkeys: L launch ({launch_status}), P plan, D doctor, Q quit")


def _prompt_checklist_action(
    manager: SetupManager,
    state,
    *,
    selected_index: int = 0,
) -> _ChecklistAction:
    items = manager.checklist(state)
    selected_index = min(max(selected_index, 0), max(len(items) - 1, 0))
    while True:
        _clear_screen()
        _render_checklist(manager, state, selected_index=selected_index)
        key = _read_key()
        if key == "up":
            selected_index = _move_selection(selected_index, -1, len(items))
            continue
        if key == "down":
            selected_index = _move_selection(selected_index, 1, len(items))
            continue
        if key in _CHECKLIST_HOTKEYS:
            return _ChecklistAction(_CHECKLIST_HOTKEYS[key], selected_index)
        if key == "enter" and items:
            return _ChecklistAction("item", selected_index, items[selected_index].id)


def _echo_guidance(*lines: str) -> None:
    typer.echo("")
    for line in lines:
        typer.echo(line)
    typer.echo("")


def _clear_screen() -> None:
    click.clear()


def _configure_item(manager: SetupManager, state, item_id: str, *, interactive: bool = False):
    if item_id == "secret_storage":
        _echo_guidance(
            "Evander keeps real provider credentials on the control-plane host.",
            "Agents receive only narrow relay access, not your API keys.",
            "For v1 the setup flow uses one local env file for those host secrets.",
        )
        typer.echo(f"Host secret file: {state.secret_file}")
        if typer.confirm("Use this secret file?", default=True):
            return manager.acknowledge_secret_storage(state)
        return state
    if item_id == "admin_web":
        _echo_guidance(
            "Admin Web is the operator UI for configuring Evander and monitoring agents.",
            "Use 127.0.0.1 when you only want browser access from this machine.",
            "Use 0.0.0.0 only when you intentionally want it reachable through the network",
            "or through a reverse proxy you control.",
            "Local password auth is recommended. No-auth mode is supported for isolated",
            "local evaluation only and gives anyone with network access full admin access.",
        )
        host = typer.prompt(
            "Admin Web host", default=str(state.admin_web.get("host") or DEFAULT_ADMIN_HOST)
        )
        port = typer.prompt(
            "Admin Web port", default=str(state.admin_web.get("port") or DEFAULT_ADMIN_PORT)
        )
        username = typer.prompt(
            "Admin username",
            default=str(state.admin_web.get("username") or getpass.getuser() or "admin"),
        )
        if interactive:
            auth_mode = _select_single(
                "Choose Admin Web authentication",
                _ADMIN_AUTH_OPTIONS,
                default_value=str(state.admin_web.get("auth_mode") or DEFAULT_ADMIN_AUTH_MODE),
            )
        else:
            typer.echo("")
            typer.echo("1. Local password (recommended)")
            typer.echo("2. No authentication (discouraged)")
            raw_auth = typer.prompt(
                "Choose Admin Web authentication",
                default="2"
                if str(state.admin_web.get("auth_mode") or DEFAULT_ADMIN_AUTH_MODE) == "none"
                else "1",
            ).strip()
            auth_mode = "none" if raw_auth == "2" else "local_password"
        password = ""
        if auth_mode == "none":
            typer.echo("")
            typer.echo("No-auth mode disables sign-in and grants full admin access to anyone")
            typer.echo("who can reach Admin Web. Use it only on an isolated test host or")
            typer.echo("with Admin Web bound to localhost.")
            if not typer.confirm("Use Admin Web without authentication?", default=False):
                return state
        else:
            password = typer.prompt("Admin password", hide_input=True, confirmation_prompt=True)
        return manager.configure_admin_web(
            state, host=host, port=port, username=username, password=password, auth_mode=auth_mode
        )
    if item_id == "execution_model":
        _echo_guidance(
            "The execution model decides where agent work runs and what isolation boundary",
            "separates agents from the control plane.",
            "Containers on this host are fastest to set up, but put the weakest outer boundary",
            "between agents and the control plane.",
            "Containers on a separate worker host are a practical middle ground.",
            "VM-backed agents give the strongest isolation, with higher setup and resource cost.",
        )
        if interactive:
            model = _select_single(
                "Choose execution model",
                _EXECUTION_OPTIONS,
                default_value=str(state.execution.get("model") or "container_same_host"),
            )
        else:
            typer.echo("1. Containers on this host")
            typer.echo("2. Containers on a separate worker host")
            typer.echo("3. VM-backed agents")
            raw = typer.prompt("Choose execution model", default="1").strip()
            model = {
                "1": "container_same_host",
                "2": "container_worker_host",
                "3": "libvirt_vm",
            }.get(raw, "container_same_host")
        return manager.configure_execution(state, model=model)
    if item_id == "ai_providers":
        _echo_guidance(
            "AI providers are the model backends Evander can route agent and assistant work to.",
            "You can select more than one provider now and choose the default model next.",
            "OpenAI and Anthropic need API keys stored in the host secret file.",
            "Ollama is for a local OpenAI-compatible Ollama server.",
        )
        if interactive:
            providers = _select_multiple(
                "Select AI providers",
                _PROVIDER_OPTIONS,
                default_values=state.ai_providers or ["openai"],
            )
        else:
            raw = typer.prompt(
                "Select providers (comma-separated: openai, anthropic, ollama)",
                default=", ".join(state.ai_providers) if state.ai_providers else "openai",
            )
            providers = [part.strip().lower() for part in raw.split(",") if part.strip()]
        secrets_by_env: dict[str, str] = {}
        for provider in providers:
            env_var = PROVIDER_ENV_VARS.get(provider)
            if not env_var:
                continue
            if typer.confirm(f"Enter value for {env_var} now?", default=True):
                secrets_by_env[env_var] = typer.prompt(env_var, hide_input=True)
        ollama_base_url = DEFAULT_OLLAMA_BASE_URL
        if "ollama" in providers:
            ollama_base_url = typer.prompt(
                "Ollama OpenAI-compatible base URL", default=DEFAULT_OLLAMA_BASE_URL
            )
        return manager.configure_ai_providers(
            state,
            providers=providers,
            secrets_by_env=secrets_by_env,
            ollama_base_url=ollama_base_url,
        )
    if item_id == "default_model":
        if not state.ai_providers:
            typer.echo("Select at least one AI provider first.")
            return state
        _echo_guidance(
            "The default model is used when an agent, assistant, or workflow does not",
            "request a more specific model.",
            "Pick the provider and model you want Evander to use for the first smoke path.",
            "You can change this later in directory-scoped configuration.",
        )
        provider = typer.prompt(
            f"Default provider ({', '.join(state.ai_providers)})",
            default=state.default_provider or state.ai_providers[0],
        ).strip()
        model = typer.prompt("Default model", default=state.default_model or "gpt-5.4")
        return manager.configure_default_model(state, provider=provider, model=model)
    if item_id == "install_preflight":
        if not manager.configuration_ready(state):
            typer.echo("Complete required configuration items before running preflight.")
            return state
        _echo_guidance(
            "Preflight writes the selected local config files and checks that the host",
            "has the pieces needed for the selected setup.",
            "It does not start the control-plane services.",
        )
        if typer.confirm("Run install preflight now?", default=True):
            try:
                return manager.run_install_preflight(state)
            except SetupPreflightError as exc:
                typer.echo("")
                typer.echo(f"Install preflight failed with exit code {exc.returncode}.")
                typer.echo("Fix the failed checks above, then rerun Install preflight.")
                return manager.load_state()
        return state
    if item_id == "launch_verify":
        return _launch(manager, state)
    typer.echo("This setup item is not configurable in v1.")
    return state


def _select_single(
    title: str,
    options: tuple[_MenuOption, ...],
    *,
    default_value: str,
) -> str:
    selected_index = _default_index(options, default_value)
    while True:
        _clear_screen()
        typer.echo(title)
        typer.echo("=" * len(title))
        for index, option in enumerate(options):
            cursor = ">" if index == selected_index else " "
            line = f"{cursor} {option.label}"
            if option.description:
                line = f"{line} - {option.description}"
            if index == selected_index:
                line = click.style(line, reverse=True)
            typer.echo(line)
        typer.echo("")
        typer.echo("Up/Down move, Enter selects.")
        key = _read_key()
        if key == "up":
            selected_index = _move_selection(selected_index, -1, len(options))
        elif key == "down":
            selected_index = _move_selection(selected_index, 1, len(options))
        elif key == "enter":
            return options[selected_index].value


def _select_multiple(
    title: str,
    options: tuple[_MenuOption, ...],
    *,
    default_values: list[str],
) -> list[str]:
    selected_index = 0
    enabled = {value for value in default_values if any(option.value == value for option in options)}
    message = ""
    while True:
        _clear_screen()
        typer.echo(title)
        typer.echo("=" * len(title))
        for index, option in enumerate(options):
            cursor = ">" if index == selected_index else " "
            checked = "[x]" if option.value in enabled else "[ ]"
            line = f"{cursor} {checked} {option.label}"
            if option.description:
                line = f"{line} - {option.description}"
            if index == selected_index:
                line = click.style(line, reverse=True)
            typer.echo(line)
        typer.echo("")
        if message:
            typer.echo(message)
        typer.echo("Up/Down move, Space toggles, Enter confirms.")
        key = _read_key()
        if key == "up":
            selected_index = _move_selection(selected_index, -1, len(options))
            message = ""
        elif key == "down":
            selected_index = _move_selection(selected_index, 1, len(options))
            message = ""
        elif key == "space":
            value = options[selected_index].value
            if value in enabled:
                enabled.remove(value)
            else:
                enabled.add(value)
            message = ""
        elif key == "enter":
            if enabled:
                return [option.value for option in options if option.value in enabled]
            message = "Select at least one provider before continuing."


def _default_index(options: tuple[_MenuOption, ...], default_value: str) -> int:
    for index, option in enumerate(options):
        if option.value == default_value:
            return index
    return 0


def _move_selection(index: int, delta: int, count: int) -> int:
    if count <= 0:
        return 0
    return (index + delta) % count


def _is_interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _read_key() -> str:
    try:
        import termios
        import tty
    except ImportError:
        return _normalize_key(click.getchar())

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return _normalize_key(_read_key_bytes(fd).decode(errors="ignore"))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _read_key_bytes(fd: int) -> bytes:
    value = os.read(fd, 1)
    if value != b"\x1b":
        return value
    tail = bytearray()
    while len(tail) < 2:
        readable, _, _ = select.select([fd], [], [], 0.05)
        if not readable:
            break
        tail.extend(os.read(fd, 1))
    return value + bytes(tail)


def _normalize_key(value: str) -> str:
    if value in {"\x1b[A", "\x1bOA"}:
        return "up"
    if value in {"\x1b[B", "\x1bOB"}:
        return "down"
    if value in {"\r", "\n"}:
        return "enter"
    if value == " ":
        return "space"
    if value == "\x03":
        raise KeyboardInterrupt
    if len(value) == 1:
        return value.lower()
    return value


def _launch(manager: SetupManager, state):
    if not manager.required_ready(state):
        typer.echo("Launch is blocked until required items and preflight are complete.")
        return state
    _echo_guidance(
        "Launch applies the setup plan, installs the systemd units, starts the core",
        "control-plane services, waits for Admin Web, and queues an Admin Assistant",
        "smoke message to prove the first agent path is wired.",
    )
    typer.echo(json.dumps(manager.plan(), indent=2, sort_keys=True))
    if not typer.confirm("Apply this setup plan and verify the Admin Assistant?", default=False):
        return state
    return manager.launch(state)
