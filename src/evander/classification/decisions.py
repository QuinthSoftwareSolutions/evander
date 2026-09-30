"""Reserved Decisions integration; no unpublished HTTP or model contract is assumed."""

from __future__ import annotations

from evander.classification.models import ClassificationError, DecisionsProviderSettings


class DecisionsProvider:
    display_name = "OpenAI Decisions"
    supported_question_types = frozenset({"choice", "score", "boolean"})
    available = False
    unavailable_reason = (
        "The OpenAI Decisions integration is not available yet. "
        "Profiles can be prepared, but evaluation requires the published API contract "
        "and a supported integration."
    )
    default_settings = DecisionsProviderSettings()
    # No probability contract is confirmed by the announcement.
    reports_option_probabilities = False
    usage_provider = "openai"
    usage_endpoint_identifier = "classification"

    def evaluate(self, request, settings, api_key, record_attempt):
        raise ClassificationError(self.unavailable_reason)
