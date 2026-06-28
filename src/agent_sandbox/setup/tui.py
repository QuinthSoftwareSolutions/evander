from __future__ import annotations

import getpass
import json
from pathlib import Path

import typer

from agent_sandbox.setup.engine import (
    DEFAULT_ADMIN_HOST,
    DEFAULT_ADMIN_PORT,
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_RUNTIME_DIR,
    PROVIDER_ENV_VARS,
    SetupManager,
)


def run_setup_tui(*, project_dir: Path, runtime_dir: Path = DEFAULT_RUNTIME_DIR) -> None:
    manager = SetupManager(project_dir=project_dir, runtime_dir=runtime_dir)
    state = manager.load_state()
    if not state.secret_storage_acknowledged:
        typer.echo("Evander keeps provider credentials on the control-plane host.")
        typer.echo(f"Secrets entered during setup will be stored in: {state.secret_file}")
        typer.echo("Secret values are not written to agents, workflow files, prompts, or setup state.")
        if typer.confirm("Continue with this host secret file?", default=True):
            manager.acknowledge_secret_storage(state)
            state = manager.load_state()
        else:
            raise typer.Exit(1)

    while True:
        _render_checklist(manager, state)
        choice = typer.prompt(
            "Select item number, L to launch when ready, P for plan, D for doctor, Q to quit",
            default="q",
        ).strip().lower()
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
        state = _configure_item(manager, state, item.id)


def _render_checklist(manager: SetupManager, state) -> None:
    typer.echo("")
    typer.echo("Evander Setup")
    typer.echo("=" * 13)
    for index, item in enumerate(manager.checklist(state), start=1):
        marker = "completed" if item.status == "completed" else item.status
        typer.echo(f"{index}. [{marker}] {item.label}: {item.summary}")
    launch_status = "available" if manager.required_ready(state) else "blocked"
    typer.echo(f"\nLaunch: {launch_status}")


def _configure_item(manager: SetupManager, state, item_id: str):
    if item_id == "secret_storage":
        typer.echo(f"Host secret file: {state.secret_file}")
        if typer.confirm("Use this secret file?", default=True):
            return manager.acknowledge_secret_storage(state)
        return state
    if item_id == "setup_profile":
        typer.echo("1. Local workstation / evaluation")
        typer.echo("2. Dedicated single-machine host")
        profile = typer.prompt("Choose setup profile", default="1").strip()
        state.setup_profile = "dedicated_single_host" if profile == "2" else "local_workstation"
        manager.save_state(state)
        return state
    if item_id == "admin_web":
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
        password = typer.prompt("Admin password", hide_input=True, confirmation_prompt=True)
        return manager.configure_admin_web(
            state, host=host, port=port, username=username, password=password
        )
    if item_id == "execution_model":
        typer.echo("1. Containers on this host")
        typer.echo("2. Containers on a separate worker host")
        typer.echo("3. VM-backed agents")
        raw = typer.prompt("Choose execution model", default="1").strip()
        model = {"1": "container_same_host", "2": "container_worker_host", "3": "libvirt_vm"}.get(
            raw, "container_same_host"
        )
        return manager.configure_execution(state, model=model)
    if item_id == "ai_providers":
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
        if typer.confirm("Run install preflight now?", default=True):
            return manager.run_install_preflight(state)
        return state
    if item_id == "launch_verify":
        return _launch(manager, state)
    typer.echo("This setup item is not configurable in v1.")
    return state


def _launch(manager: SetupManager, state):
    if not manager.required_ready(state):
        typer.echo("Launch is blocked until required items and preflight are complete.")
        return state
    typer.echo(json.dumps(manager.plan(), indent=2, sort_keys=True))
    if not typer.confirm("Apply this setup plan and verify the Admin Assistant?", default=False):
        return state
    return manager.launch(state)
