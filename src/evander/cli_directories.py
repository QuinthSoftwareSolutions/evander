"""Evander directories commands."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from . import cli_app, cli_common, cli_runtime


@cli_app.directories_app.command("list", short_help="List directory children.")
def directories_list(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    parent: Annotated[str, typer.Option("--parent", help="Parent directory path or id.")] = "root",
) -> None:
    admin_runtime = cli_runtime.build_admin_runtime(config)
    result = admin_runtime.directory_admin.list(parent)
    cli_common._echo_admin_result_or_exit(result)
    payload = result.data
    assert payload is not None
    typer.echo(json.dumps(payload.model_dump(mode="json"), indent=2, sort_keys=True))


@cli_app.directories_app.command("show", short_help="Show one directory and its ancestry.")
def directories_show(
    reference: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    admin_runtime = cli_runtime.build_admin_runtime(config)
    result = admin_runtime.directory_admin.get(reference)
    cli_common._echo_admin_result_or_exit(result)
    payload = result.data
    assert payload is not None
    typer.echo(json.dumps(payload.model_dump(mode="json"), indent=2, sort_keys=True))


@cli_app.directories_app.command("create", short_help="Create a child directory.")
def directories_create(
    name: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    parent: Annotated[str, typer.Option("--parent", help="Parent directory path or id.")] = "root",
) -> None:
    admin_runtime = cli_runtime.build_admin_runtime(config)
    result = admin_runtime.directory_admin.create_child(parent=parent, name=name)
    cli_common._echo_admin_result_or_exit(result)
    assert result.data is not None
    typer.echo(result.data.details.directory.path)


@cli_app.directories_app.command("rename", short_help="Rename a directory.")
def directories_rename(
    reference: str,
    new_name: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    admin_runtime = cli_runtime.build_admin_runtime(config)
    result = admin_runtime.directory_admin.update(reference, new_name=new_name)
    cli_common._echo_admin_result_or_exit(result)
    assert result.data is not None
    typer.echo(result.data.details.directory.path)


@cli_app.directories_app.command("move", short_help="Move a directory to a new parent.")
def directories_move(
    reference: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    new_parent: Annotated[
        str, typer.Option("--new-parent", help="New parent directory path or id.")
    ] = "root",
) -> None:
    admin_runtime = cli_runtime.build_admin_runtime(config)
    result = admin_runtime.directory_admin.update(reference, new_parent=new_parent)
    cli_common._echo_admin_result_or_exit(result)
    assert result.data is not None
    typer.echo(result.data.details.directory.path)


@cli_app.directories_app.command("delete", short_help="Delete a directory.")
def directories_delete(
    reference: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    recursive: Annotated[
        bool, typer.Option("--recursive", help="Delete non-empty directories recursively.")
    ] = False,
) -> None:
    admin_runtime = cli_runtime.build_admin_runtime(config)
    result = admin_runtime.directory_admin.delete(reference, recursive=recursive)
    cli_common._echo_admin_result_or_exit(result)
    assert result.data is not None
    typer.echo(json.dumps(result.data.model_dump(mode="json"), indent=2, sort_keys=True))


@cli_app.directories_app.command(
    "config-show", short_help="Show raw and effective config for one directory domain."
)
def directories_config_show(
    reference: str,
    domain: Annotated[str, typer.Option("--domain", help="Directory config domain name.")],
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    admin_runtime = cli_runtime.build_admin_runtime(config)
    operations = admin_runtime.operations_admin
    raw_result = operations.read_directory_domain_config(reference, domain)
    effective_result = operations.compute_directory_effective_config(reference, domain)
    cli_common._echo_admin_result_or_exit(raw_result)
    cli_common._echo_admin_result_or_exit(effective_result)
    assert raw_result.data is not None
    assert effective_result.data is not None
    typer.echo(
        json.dumps(
            {
                "raw": raw_result.data.model_dump(mode="json"),
                "effective": effective_result.data.model_dump(mode="json"),
                "validation_diagnostics": [
                    item.model_dump(mode="json") for item in effective_result.validation_diagnostics
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


@cli_app.directories_app.command(
    "config-set", short_help="Write raw config for one directory domain."
)
def directories_config_set(
    reference: str,
    payload: str,
    domain: Annotated[str, typer.Option("--domain", help="Directory config domain name.")],
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"invalid JSON payload: {exc}") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter("config payload must be a JSON object")
    admin_runtime = cli_runtime.build_admin_runtime(config)
    result = admin_runtime.operations_admin.write_directory_domain_config(reference, domain, parsed)
    cli_common._echo_admin_result_or_exit(result)
    assert result.data is not None
    typer.echo(json.dumps(result.data.model_dump(mode="json"), indent=2, sort_keys=True))
