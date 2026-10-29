"""Host-only capability restriction for untrusted participant support agents."""

from dataclasses import dataclass
from typing import Mapping

from evander.models import SessionManifest


@dataclass(frozen=True)
class AgentCapabilityRestriction:
    tools: tuple[str, ...]
    binding: Mapping[str, str]

    def apply(self, session: SessionManifest) -> SessionManifest:
        # Construct from a positive list so new manifest capabilities default off.
        identity = {
            key: getattr(session, key)
            for key in (
                "session_id",
                "vm_id",
                "vm_provider",
                "vm_provider_id",
                "model",
                "cost_category",
                "reasoning_effort",
                "fast_mode",
                "codex_auth_mode",
                "subscription_profile_id",
                "directory_path",
                "expires_at",
                "created_at",
            )
        }
        metadata = {
            key: value
            for key, value in session.metadata.items()
            if key
            in {
                "execution_backend",
                "target_name",
                "agent_provider",
                "model_provider_mode",
                "container_worker",
                "worker_url",
            }
        }
        metadata["capability_binding"] = dict(self.binding)
        return SessionManifest(
            **identity,
            allowed_tools=list(self.tools),
            allowed_contact_ids=[],
            communication_target_ids=[],
            metadata=metadata,
        )
