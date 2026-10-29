from .cursor_store import DurableCursorStore, EmailSyncCursor
from .gmail_provider import GmailEmailProvider
from .providers import (
    EmailAttachmentContent,
    EmailAttachmentMetadata,
    EmailDraft,
    EmailDraftAttachment,
    EmailMessageDetail,
    EmailMessageSummary,
    EmailProvider,
    JsonEmailProvider,
    MultiMailboxEmailProvider,
)

__all__ = [
    "EmailAttachmentContent",
    "EmailAttachmentMetadata",
    "EmailDraft",
    "EmailDraftAttachment",
    "EmailMessageDetail",
    "EmailMessageSummary",
    "EmailProvider",
    "EmailSyncCursor",
    "DurableCursorStore",
    "GmailEmailProvider",
    "GraphEmailProvider",
    "JsonEmailProvider",
    "MultiMailboxEmailProvider",
]


def __getattr__(name):
    if name == "GraphEmailProvider":
        from evander.optional_integrations import require_teams_graph_module

        return require_teams_graph_module("evander_teams_graph_addon.email").GraphEmailProvider
    raise AttributeError(name)
