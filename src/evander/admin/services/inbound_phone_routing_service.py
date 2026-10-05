from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from evander.config import InboundPhoneRoute, _normalize_phone_number
from evander.directories import DirectoryService

if TYPE_CHECKING:
    from evander.runtime import RuntimeContext


logger = logging.getLogger(__name__)


def phone_match_key(value: str | None) -> str | None:
    number = _normalize_phone_number(value, field_name="inbound_phone_calls")
    return number.removeprefix("+") if number else None


@dataclass(frozen=True)
class InboundPhoneBinding:
    route: InboundPhoneRoute
    session_id: str
    directory_path: str


class InboundPhoneRoutingService:
    """Select the origin session without using caller access policies as routing rules."""

    def __init__(self, runtime: RuntimeContext) -> None:
        self.runtime = runtime

    def list_routes(self, *, skip_invalid_directories: bool = False) -> list[InboundPhoneRoute]:
        routes: list[InboundPhoneRoute] = []
        seen: set[str] = set()
        directory_service = DirectoryService(self.runtime.directories)
        paths = sorted(
            {
                directory_service.show(directory_id).directory.path
                for directory_id in self.runtime.directories.read_all()
            }
        )
        for path in paths:
            try:
                config = self.runtime.provider_config.resolve_for_directory(path)
            except (ValueError, OSError):
                if not skip_invalid_directories:
                    raise
                logger.warning(
                    "Skipping inbound phone route options for invalid directory configuration: %s",
                    path,
                )
                continue
            for route in config.inbound_phone_routes:
                signature = json.dumps(route.model_dump(mode="json"), sort_keys=True)
                if signature not in seen:
                    seen.add(signature)
                    routes.append(route)
        return routes

    def resolve(
        self,
        *,
        route_id: str | None,
        did: str,
        caller_number: str,
    ) -> InboundPhoneBinding:
        did_key = phone_match_key(did)
        caller_key = phone_match_key(caller_number)
        root = self.runtime.provider_config.resolve_for_directory("root")
        caller_map = next(
            (
                entry
                for entry in root.inbound_phone_caller_maps
                if phone_match_key(entry.did) == did_key
            ),
            None,
        )
        assignment = None
        if caller_map is not None:
            if route_id and route_id != caller_map.route_id:
                raise ValueError("Inbound phone route conflicts with the configured caller map")
            route_id = caller_map.route_id
            assignment = next(
                (
                    entry
                    for entry in caller_map.callers
                    if phone_match_key(entry.caller_number) == caller_key
                ),
                None,
            )
            if assignment is None:
                raise PermissionError("Caller has no assignment for this inbound number")

        routes = self.list_routes()
        if not routes:
            raise LookupError("No inbound phone routes are configured")
        matches = [
            route
            for route in routes
            if (not route_id or route.id == route_id)
            and did_key in {phone_match_key(route.did), phone_match_key(route.called_number)}
        ]
        if not matches:
            raise LookupError("No inbound phone route matched the request")
        if len(matches) > 1:
            raise LookupError("Inbound phone route match is ambiguous")
        route = matches[0]
        return InboundPhoneBinding(
            route=route,
            session_id=assignment.session_id if assignment else route.session_id,
            directory_path=assignment.directory_path if assignment else route.directory_path,
        )

    @staticmethod
    def caller_policy_error(route: InboundPhoneRoute, *, caller_number: str) -> str | None:
        caller = phone_match_key(caller_number)
        blocked = {phone_match_key(number) for number in route.blocked_callers}
        allowed = {phone_match_key(number) for number in route.allowed_callers}
        if caller in blocked:
            return "Caller is blocked for this inbound route"
        if allowed and caller not in allowed:
            return "Caller is not allowlisted for this inbound route"
        return None
