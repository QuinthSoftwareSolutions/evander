from __future__ import annotations

from .base import QueryProviderBase


class PullRequestGetQueryProvider(QueryProviderBase):
    """Placeholder query provider for fetching a single pull request."""

    NODE_TYPE = "query:pull-request:get"
