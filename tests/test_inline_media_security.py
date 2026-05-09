import pytest

from agent_sandbox.inline_media import (
    download_url_with_limit,
    require_provider_media_url,
    sanitize_inline_media_metadata,
)


def test_inline_media_metadata_sanitization_is_recursive_and_case_insensitive() -> None:
    assert sanitize_inline_media_metadata(
        {
            "Authorization": "Bearer secret",
            "name": "screenshot.png",
            "headers": {"accessToken": "secret", "safe": "value"},
            "items": [{"content-base64": "abc", "title": "kept"}],
        }
    ) == {
        "name": "screenshot.png",
        "headers": {"safe": "value"},
        "items": [{"title": "kept"}],
    }


def test_github_inline_media_url_requires_https_allowlisted_host() -> None:
    require_provider_media_url(
        provider="github",
        url="https://private-user-images.githubusercontent.com/u/1/image.png",
    )

    with pytest.raises(ValueError, match="must be https"):
        require_provider_media_url(provider="github", url="http://github.com/image.png")

    with pytest.raises(ValueError, match="not allowlisted"):
        require_provider_media_url(
            provider="github",
            url="https://169.254.169.254/latest/meta-data/",
        )


def test_azure_devops_inline_media_url_uses_configured_host_allowlist() -> None:
    require_provider_media_url(
        provider="ado",
        url="https://dev.azure.com/example/_apis/wit/attachments/1",
        allowed_hosts=["dev.azure.com"],
    )

    with pytest.raises(ValueError, match="not allowlisted"):
        require_provider_media_url(
            provider="azure_devops",
            url="https://attacker.example/download",
            allowed_hosts=["dev.azure.com"],
        )


def test_download_inline_media_follows_validated_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeResponse:
        def __init__(
            self,
            status_code: int,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            content: bytes = b"",
        ) -> None:
            self.status_code = status_code
            self.url = url
            self.headers = headers or {}
            self._content = content

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        @property
        def is_redirect(self) -> bool:
            return self.status_code in {301, 302, 303, 307, 308}

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise AssertionError(f"unexpected status {self.status_code}")

        def iter_bytes(self):
            yield self._content

    responses = [
        FakeResponse(
            302,
            "https://github.com/user-attachments/assets/abc",
            headers={"location": "https://private-user-images.githubusercontent.com/u/1/image.png"},
        ),
        FakeResponse(
            200,
            "https://private-user-images.githubusercontent.com/u/1/image.png",
            headers={"content-type": "image/png; charset=utf-8"},
            content=b"png-bytes",
        ),
    ]

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def stream(self, method: str, url: str, **kwargs: object) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            return responses.pop(0)

    monkeypatch.setattr("agent_sandbox.inline_media.httpx.Client", FakeClient)

    content, mime_type = download_url_with_limit(
        url="https://github.com/user-attachments/assets/abc",
        headers={"Authorization": "Bearer secret", "X-Keep": "1"},
        provider="github",
    )

    assert content == b"png-bytes"
    assert mime_type == "image/png"
    assert [call["url"] for call in calls] == [
        "https://github.com/user-attachments/assets/abc",
        "https://private-user-images.githubusercontent.com/u/1/image.png",
    ]
    assert calls[1]["headers"] == {"X-Keep": "1"}


def test_download_inline_media_rejects_unvalidated_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        status_code = 302
        url = "https://github.com/user-attachments/assets/abc"
        headers = {"location": "https://169.254.169.254/latest/meta-data"}
        is_redirect = True

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def stream(self, method: str, url: str, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr("agent_sandbox.inline_media.httpx.Client", FakeClient)

    with pytest.raises(ValueError, match="not allowlisted"):
        download_url_with_limit(
            url="https://github.com/user-attachments/assets/abc",
            provider="github",
        )
