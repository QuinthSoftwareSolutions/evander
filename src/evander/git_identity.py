from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GitIdentity:
    name: str
    email: str


DEFAULT_AGENT_GIT_IDENTITY = GitIdentity(
    name="Evander Bot",
    email="evander@quinth.se",
)
TEAM_DEPENDENCY_COMPOSER_GIT_IDENTITY = GitIdentity(
    name="Evander Team Dependency Composer",
    email="team-dependencies@evander.invalid",
)
