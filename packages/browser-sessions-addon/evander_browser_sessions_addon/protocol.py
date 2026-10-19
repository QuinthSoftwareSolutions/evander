"""Authorization is checked by the broker AND the executing browser endpoint."""

from __future__ import annotations
from .models import BrowserCommand, BrowserError, Session, origin


def authorize_command(session: Session, actor: str, command: BrowserCommand, *, agent=False):
    if command.generation != session.control_generation:
        raise BrowserError("Browser control changed; observe again", "stale_control", 409)
    if not command.observing and session.controller != actor:
        raise BrowserError("Browser control has not been granted", "control_required", 403)
    if session.state not in {"ready", "paused"}:
        raise BrowserError("Browser is disconnected", "disconnected", 409)
    if command.action == "navigate" and (
        not command.url or origin(command.url) not in session.allowed_origins
    ):
        raise BrowserError(
            "The destination is outside the approved application", "origin_denied", 403
        )
    if (
        not command.observing
        and command.action not in {"navigate", "reload"}
        and not command.observation_id
    ):
        raise BrowserError("Observe the page before interacting", "observation_required", 409)
    if agent and not session.allowed_origins:
        raise BrowserError(
            "A local user must approve an application origin", "origin_required", 403
        )
