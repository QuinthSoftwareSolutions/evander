"""Shared test adapter; every call retains its caller's fresh runtime/transport."""

from __future__ import annotations

import httpx
import pytest


def patch_httpx_client(
    monkeypatch: pytest.MonkeyPatch,
    transport: httpx.BaseTransport,
) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        "evander.services.httpx.Client",
        lambda timeout: real_client(timeout=timeout, transport=transport),
    )
