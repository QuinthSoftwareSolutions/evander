from __future__ import annotations

from .base import QueryProviderBase


class IssueListQueryProvider(QueryProviderBase):
    NODE_TYPE = "query:issue:list"
