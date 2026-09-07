from __future__ import annotations

from evander.triggers.providers.base import TriggerProviderBase


class AgentRunCompletedEventTriggerProvider(TriggerProviderBase):
    """Declare an externally published agent-completion event source.

    The dispatch-owning prompt effect or wake monitor publishes completion
    events directly. The effect worker forwards them to enabled completion
    triggers within their configured scope. This provider deliberately performs
    no host inventory polling.
    """
