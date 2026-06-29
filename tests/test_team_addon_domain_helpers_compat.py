from __future__ import annotations

from evander_team_addon import providers
from evander_team_addon.services import domain_helpers
from evander_team_addon.services.domain import review


def test_providers_import_and_legacy_review_helpers_reexported() -> None:
    assert providers.TeamWakeDueAgentsEffectProvider.__module__ == "evander_team_addon.providers"
    assert domain_helpers._apply_review_request is review._apply_review_request
    assert domain_helpers._review_request_was_created is review._review_request_was_created
    assert set(domain_helpers.__all__) == {
        "_apply_review_request",
        "_review_request_was_created",
    }
