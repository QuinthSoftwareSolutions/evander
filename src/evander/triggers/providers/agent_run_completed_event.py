from __future__ import annotations

from evander.triggers.providers.base import TriggerProviderBase


class AgentRunCompletedEventTriggerProvider(TriggerProviderBase):
    """Declare an externally published agent-completion event source.

    The dispatch-owning prompt effect or wake monitor publishes completion
    events directly. This provider deliberately performs no host inventory
    polling; it only keeps existing workflow configurations loadable until
    their runtime pack is refreshed.
    """
