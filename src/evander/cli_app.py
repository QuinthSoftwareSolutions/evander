"""Typer application registry and grouped help ordering."""

from __future__ import annotations

import typer
from typer.core import TyperGroup

from evander.code_index.cli import app as code_index_app

_COMMAND_PANELS = {
    "Health and State": 0,
    "Sessions and Feedback": 1,
    "Issue Lifecycle": 2,
    "VM Operations": 3,
    "Seeding and Fixtures": 4,
    "Background Workers": 5,
}


class GroupedHelpTyperGroup(TyperGroup):
    def list_commands(self, ctx) -> list[str]:
        def command_sort_key(name: str) -> tuple[int, str]:
            command = self.commands[name]
            panel = getattr(command, "rich_help_panel", None)
            panel_rank = _COMMAND_PANELS.get(panel or "", len(_COMMAND_PANELS))
            return (panel_rank, name.casefold())

        return sorted(self.commands, key=command_sort_key)


app = typer.Typer(help="Utilities for the Evander host control plane.", cls=GroupedHelpTyperGroup)
directories_app = typer.Typer(help="Directory management and directory-scoped config inspection.")
git_repos_app = typer.Typer(help="Manage Evander-owned bare Git repositories.")
wake_schedules_app = typer.Typer(help="Manage scheduled wake requests.")
teams_auth_app = typer.Typer(help="Manage delegated Teams auth.")
teams_meeting_app = typer.Typer(help="Manage live Teams meeting agent sessions.")
execution_app = typer.Typer(help="Manual execution backend controls.")
connectors_app = typer.Typer(help="Inspect, validate, and test directory-scoped connectors.")
setup_app = typer.Typer(
    help="Guided first-run setup for the Evander control plane.",
    invoke_without_command=True,
)
team_app = typer.Typer(help="Inspect Team Add-on runtime activity.")

app.add_typer(directories_app, name="directories")
app.add_typer(git_repos_app, name="git-repos", rich_help_panel="Health and State")
app.add_typer(code_index_app, name="code-index", rich_help_panel="Health and State")
app.add_typer(wake_schedules_app, name="wake-schedules")
app.add_typer(teams_auth_app, name="teams-auth", rich_help_panel="Sessions and Feedback")
app.add_typer(teams_meeting_app, name="teams-meeting", rich_help_panel="Sessions and Feedback")
app.add_typer(execution_app, name="execution", rich_help_panel="VM Operations")
app.add_typer(connectors_app, name="connectors", rich_help_panel="Health and State")
app.add_typer(setup_app, name="setup", rich_help_panel="Health and State")
app.add_typer(team_app, name="team", rich_help_panel="Health and State")
