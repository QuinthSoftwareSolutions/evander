from __future__ import annotations

import pytest

from agent_sandbox.config import GatewayConfig


def test_inbound_phone_routes_normalize_numbers_and_round_trip_through_config_model() -> None:
    config = GatewayConfig.model_validate(
        {
            "inbound_phone_routes": [
                {
                    "id": " ops-main ",
                    "called_number": " +1 (555) 000-0100 ",
                    "directory_path": "root/ops",
                    "session_id": " origin-session ",
                    "allowed_callers": [" +1 (555) 000-0200 ", "+15550000200"],
                    "blocked_callers": ["(555) 000-0999"],
                    "route_prompt": "Answer inbound calls for ops.",
                    "startup_text": "Thanks for calling ops.",
                }
            ]
        }
    )

    assert config.inbound_phone_routes[0].id == "ops-main"
    assert config.inbound_phone_routes[0].called_number == "+15550000100"
    assert config.inbound_phone_routes[0].directory_path == "root/ops"
    assert config.inbound_phone_routes[0].session_id == "origin-session"
    assert config.inbound_phone_routes[0].allowed_callers == ["+15550000200"]
    assert config.inbound_phone_routes[0].blocked_callers == ["5550000999"]
    assert config.inbound_phone_routes[0].route_prompt == "Answer inbound calls for ops."
    assert config.inbound_phone_routes[0].startup_text == "Thanks for calling ops."

    round_trip = GatewayConfig.model_validate(config.model_dump(mode="json"))

    assert round_trip.inbound_phone_routes[0].called_number == "+15550000100"
    assert round_trip.inbound_phone_routes[0].allowed_callers == ["+15550000200"]


def test_inbound_phone_routes_reject_duplicate_ids() -> None:
    with pytest.raises(
        ValueError, match="Duplicate inbound_phone_routes ids are not allowed: ops-main"
    ):
        GatewayConfig.model_validate(
            {
                "inbound_phone_routes": [
                    {
                        "id": "ops-main",
                        "did": "+15550000100",
                        "directory_path": "root/ops",
                        "session_id": "origin-session",
                        "allowed_callers": ["+15550000200"],
                        "route_prompt": "Answer inbound calls for ops.",
                    },
                    {
                        "id": "ops-main",
                        "did": "+15550000101",
                        "directory_path": "root/ops",
                        "session_id": "origin-session-2",
                        "allowed_callers": ["+15550000201"],
                        "route_prompt": "Answer inbound calls for ops backups.",
                    },
                ]
            }
        )


@pytest.mark.parametrize(
    "payload, message",
    [
        (
            {
                "id": "ops-main",
                "did": "+15550000100",
                "directory_path": "root/ops",
                "route_prompt": "Answer inbound calls for ops.",
            },
            "session_id",
        ),
        (
            {
                "id": "ops-main",
                "directory_path": "root/ops",
                "session_id": "origin-session",
                "route_prompt": "Answer inbound calls for ops.",
            },
            "define did or called_number",
        ),
    ],
)
def test_inbound_phone_routes_reject_missing_session_or_match_criteria(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        GatewayConfig.model_validate({"inbound_phone_routes": [payload]})
