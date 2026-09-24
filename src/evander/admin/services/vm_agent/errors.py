class ContainerWorkerAgentNotFoundError(ValueError):
    """No session or execution record identifies the requested container agent."""

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        super().__init__(f"Unknown container worker agent target: {agent_id}")
