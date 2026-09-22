from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
from typing import Annotated

import typer

from .store import CredentialError, CredentialStore

app = typer.Typer(
    help="Manage local encrypted credential custody without displaying secret values."
)
Runtime = Annotated[Path, typer.Option("--runtime-dir")]
Key = Annotated[
    Path | None, typer.Option("--key-file", help="Protected key path, never the key value.")
]


def _print(value):
    typer.echo(json.dumps(value, indent=2))


@app.command("init")
def initialize(
    runtime_dir: Runtime = Path("runtime"),
    key_file: Key = None,
    store_user: Annotated[str | None, typer.Option("--store-user")] = None,
):
    try:
        account = pwd.getpwnam(store_user) if store_user else None
        store = CredentialStore(runtime_dir, key_file=key_file)
        result = store.initialize()
        if account:
            for path in (store.root, store.path, store.root / "maintenance.lock"):
                os.chown(path, account.pw_uid, account.pw_gid, follow_symlinks=False)
        _print(result)
    except (CredentialError, OSError, KeyError):
        typer.echo(
            "Credential initialization failed; check protected paths, ownership and the original key.",
            err=True,
        )
        raise typer.Exit(1) from None


@app.command("status")
def status(runtime_dir: Runtime = Path("runtime"), key_file: Key = None):
    try:
        store = CredentialStore(runtime_dir, key_file=key_file)
        _print({"store": store.health(), "credentials": store.list_metadata()})
    except CredentialError:
        typer.echo("Credential metadata is unavailable.", err=True)
        raise typer.Exit(1) from None


@app.command("rotate-key")
def rotate_key(
    new_key_file: Annotated[Path, typer.Option("--new-key-file")],
    runtime_dir: Runtime = Path("runtime"),
    key_file: Key = None,
    maintenance: Annotated[bool, typer.Option("--maintenance")] = False,
):
    if not maintenance:
        typer.echo(
            "Stop credential consumers and pass --maintenance. Keep the current key until restart verification.",
            err=True,
        )
        raise typer.Exit(1)
    try:
        _print(CredentialStore(runtime_dir, key_file=key_file).rotate_key(new_key_file))
    except (CredentialError, OSError):
        typer.echo(
            "Credential key rotation failed. Retain both key files and verify store status before restarting.",
            err=True,
        )
        raise typer.Exit(1) from None


@app.command("migrate")
def migrate(
    family: Annotated[str, typer.Option("--family")],
    runtime_dir: Runtime = Path("runtime"),
    key_file: Key = None,
    directory: Annotated[str, typer.Option("--directory")] = "root",
    profile_id: Annotated[str, typer.Option("--profile-id")] = "",
    apply: Annotated[bool, typer.Option("--apply")] = False,
    maintenance: Annotated[bool, typer.Option("--maintenance")] = False,
):
    from evander.admin.composition import build_admin_runtime
    from .migration import migrate_connector, migrate_teams

    if family not in {"teams", "connector"}:
        raise typer.BadParameter("family must be teams or connector")
    if apply and not maintenance:
        typer.echo(
            "Stop affected consumers and pass --maintenance before applying migration.", err=True
        )
        raise typer.Exit(1)
    try:
        runtime = build_admin_runtime(runtime_dir)
        # Composition shares this store with connector operations and diagnostics.
        runtime.credential_admin.store.key_file = key_file
        if family == "teams":
            result = migrate_teams(runtime, apply=apply, maintenance=maintenance)
        else:
            directory = runtime.teams_admin.normalize_directory_reference(directory)
            result = migrate_connector(
                runtime, directory=directory, profile_id=profile_id, apply=apply
            )
        _print(result)
        if result.get("conflicts"):
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception:
        typer.echo(
            "Credential migration failed. Source values were not printed; inspect configuration, key access and migration metadata before retrying.",
            err=True,
        )
        raise typer.Exit(1) from None
