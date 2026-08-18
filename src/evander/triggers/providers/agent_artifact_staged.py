from __future__ import annotations

from evander.events.agent_artifacts import is_agent_artifact_staged_event
from evander.events.internal import InternalEvent
from evander.events.models import TriggerDefinitionDiscriminated, WorkflowNodeEnvelope
from evander.events.provider_models import AgentArtifactStagedTriggerDefinition
from evander.models import DirectoryPath
from evander.triggers.providers.event import EventTriggerProvider


class AgentArtifactStagedTriggerProvider(EventTriggerProvider):
    """Publish activations for successful agent ``stage_artifact`` calls."""

    _CURSOR_FILENAME = "trigger-cursors-agent-artifact-staged.json"

    @classmethod
    def validate_definition(
        cls, trigger_definition: TriggerDefinitionDiscriminated
    ) -> TriggerDefinitionDiscriminated:
        if isinstance(trigger_definition, WorkflowNodeEnvelope):
            return AgentArtifactStagedTriggerDefinition.model_validate(
                {
                    "id": trigger_definition.id,
                    "display_name": trigger_definition.display_name,
                    "type": trigger_definition.type,
                    "outputs": trigger_definition.outputs,
                    **dict(trigger_definition.config),
                }
            )
        return super().validate_definition(trigger_definition)

    def _trigger(self) -> AgentArtifactStagedTriggerDefinition:
        if not isinstance(self.trigger_definition, AgentArtifactStagedTriggerDefinition):
            raise RuntimeError(
                "AgentArtifactStagedTriggerProvider initialized with wrong definition"
            )
        return self.trigger_definition

    def _matches(self, event: InternalEvent) -> bool:
        if not is_agent_artifact_staged_event(event):
            return False
        if not self._event_directory_is_in_scope(event):
            return False
        artifact_path = str(event.payload.get("artifact_path") or "").strip()
        trigger = self._trigger()
        if trigger.artifact_path is not None:
            return artifact_path == trigger.artifact_path
        if trigger.path_prefix is not None:
            return artifact_path == trigger.path_prefix or artifact_path.startswith(
                f"{trigger.path_prefix}/"
            )
        return True

    def _event_directory_is_in_scope(self, event: InternalEvent) -> bool:
        if self.runtime_context is None:
            raise RuntimeError("AgentArtifactStagedTriggerProvider has no runtime context")
        event_directory = event.payload.get("directory_path")
        if event_directory is None:
            return False
        try:
            scope = DirectoryPath(
                segments=str(self.runtime_context.metadata.get("directory_path") or "root")
            ).as_posix()
            candidate = DirectoryPath(segments=str(event_directory)).as_posix()
        except ValueError:
            return False
        return scope == "root" or candidate == scope or candidate.startswith(f"{scope}/")
