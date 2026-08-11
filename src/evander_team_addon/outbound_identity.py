from __future__ import annotations

from typing import Literal, cast

from evander.addons import AddonContext
from evander.outbound_identity import (
    OutboundAvatar,
    OutboundIdentity,
    OutboundIdentityProvider,
    OutboundIdentityResolver,
)

from .avatars import AvatarStorage
from .repository import TeamStateRepository


def build_outbound_identity_resolver(context: AddonContext) -> OutboundIdentityResolver:
    repository = TeamStateRepository(context.runtime_dir)
    avatar_storage = AvatarStorage(context.runtime_dir)

    def resolve(
        *,
        session_id: str,
        provider: OutboundIdentityProvider,
        destination: str,
    ) -> OutboundIdentity | None:
        del destination
        if provider != "discord":
            return None
        matches = [
            agent
            for agent in repository.read().agents.values()
            if agent.session_id == str(session_id).strip()
        ]
        if len(matches) != 1:
            return None
        agent = matches[0]
        avatar = None
        if agent.avatar is not None:
            avatar = OutboundAvatar.from_bytes(
                avatar_storage.asset_path(agent.avatar.asset_id).read_bytes(),
                mime_type=cast(
                    Literal["image/png", "image/jpeg", "image/webp"],
                    agent.avatar.mime_type,
                ),
                expected_sha256=agent.avatar.sha256,
            )
        return OutboundIdentity(
            display_name=agent.display_name or agent.id,
            avatar=avatar,
            source="team-addon",
        )

    return resolve
