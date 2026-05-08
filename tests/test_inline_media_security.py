import pytest

from agent_sandbox.inline_media import (
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
