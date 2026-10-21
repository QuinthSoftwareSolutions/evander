from __future__ import annotations

from .base import EffectProviderBase, scoped_effect_directory_path


class SessionResetContextEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config=None):
        effect = self.effect_definition
        payload = {
            key: getattr(effect, key)
            for key in ("target", "conversation_key", "request_id")
        }
        payload.update(resolved_config or {})
        context = self.runtime_context
        resetter = context.session_context_resetter
        if resetter is None:
            raise RuntimeError("Session context reset service is unavailable")
        return dict(
            resetter.session_reset_context(
                **payload, directory_path=scoped_effect_directory_path(context)
            )
        )
