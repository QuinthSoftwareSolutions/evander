import os
from pathlib import Path

import httpx
from starlette.testclient import TestClient

from evander.config import GatewayConfig
from evander.directory_config import DirectoryConfigStore
from evander.directories import DirectoryStore
from evander.relays.nuget import create_app


def make_config(tmp_path: Path) -> GatewayConfig:
    return GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
        nuget_public_feed_base_url="https://api.nuget.org",
        nuget_azure_feed_base_url=(
            "https://pkgs.dev.azure.com/example/example/_packaging/example/nuget"
        ),
        nuget_azure_pat_env="AZURE_ARTIFACTS_PAT",
    )


def write_provider_overlay(config: GatewayConfig, payload: dict[str, object]) -> None:
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir).write(
        "root",
        domain="providers",
        payload=payload,
    )


def test_azure_proxy_returns_503_without_pat(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    app = create_app(config)
    client = TestClient(app)
    os.environ.pop("AZURE_ARTIFACTS_PAT", None)

    response = client.get("/nuget/azure/v3/index.json")

    assert response.status_code == 503
    assert "AZURE_ARTIFACTS_PAT" in response.text


def test_azure_proxy_injects_basic_auth(tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["AZURE_ARTIFACTS_PAT"] = "test-pat"

    response = client.get("/nuget/azure/v3/index.json")

    assert response.status_code == 200
    assert captured["authorization"].startswith("Basic ")


def test_azure_proxy_rejects_encoded_dot_segment_escape_without_sending_pat(
    tmp_path: Path,
) -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["AZURE_ARTIFACTS_PAT"] = "test-pat"

    response = client.get("/nuget/azure/%2e%2e/_apis/projects")

    assert response.status_code == 400
    assert "dot segments" in response.text
    assert called is False


def test_azure_proxy_rejects_packaging_path_outside_configured_feed(
    tmp_path: Path,
) -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["AZURE_ARTIFACTS_PAT"] = "test-pat"

    response = client.get("/nuget/azure/example/example/_packaging/other-feed/nuget/v3/index.json")

    assert response.status_code == 400
    assert "outside the configured feed" in response.text
    assert called is False


def test_azure_proxy_allows_packaging_path_under_configured_feed(
    tmp_path: Path,
) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["AZURE_ARTIFACTS_PAT"] = "test-pat"

    response = client.get("/nuget/azure/example/example/_packaging/example/nuget/v3/index.json")

    assert response.status_code == 200
    assert captured["authorization"].startswith("Basic ")
    assert (
        captured["url"]
        == "https://pkgs.dev.azure.com/example/example/_packaging/example/nuget/v3/index.json"
    )


def test_azure_proxy_appends_relative_packaging_package_paths_to_configured_feed(
    tmp_path: Path,
) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["AZURE_ARTIFACTS_PAT"] = "test-pat"

    response = client.get("/nuget/azure/v3/flat2/_packaging/index.json")

    assert response.status_code == 200
    assert captured["authorization"].startswith("Basic ")
    assert (
        captured["url"]
        == "https://pkgs.dev.azure.com/example/example/_packaging/example/nuget/v3/flat2/_packaging/index.json"
    )


def test_azure_proxy_allows_advertised_feed_id_resource_paths(tmp_path: Path) -> None:
    captured_urls: list[str] = []
    feed_id = "11111111-1111-1111-1111-111111111111"

    def handler(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        if str(request.url).endswith("/_packaging/example/nuget/v3/index.json"):
            return httpx.Response(
                200,
                json={
                    "resources": [
                        {
                            "@id": (
                                "https://pkgs.dev.azure.com/example/example/"
                                f"_packaging/{feed_id}/nuget/v3/flat2/"
                            ),
                            "@type": "PackageBaseAddress/3.0.0",
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["AZURE_ARTIFACTS_PAT"] = "test-pat"

    index_response = client.get("/nuget/azure/v3/index.json")
    assert index_response.status_code == 200
    resource_url = index_response.json()["resources"][0]["@id"]
    assert (
        resource_url
        == f"http://testserver/nuget/azure/example/example/_packaging/{feed_id}/nuget/v3/flat2/"
    )

    response = client.get(
        f"/nuget/azure/example/example/_packaging/{feed_id}/nuget/v3/flat2/pkg/index.json"
    )

    assert response.status_code == 200
    assert (
        captured_urls[-1]
        == f"https://pkgs.dev.azure.com/example/example/_packaging/{feed_id}/nuget/v3/flat2/pkg/index.json"
    )


def test_azure_proxy_rejects_unadvertised_feed_id_resource_paths(tmp_path: Path) -> None:
    called = False
    feed_id = "22222222-2222-2222-2222-222222222222"

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["AZURE_ARTIFACTS_PAT"] = "test-pat"

    response = client.get(
        f"/nuget/azure/example/example/_packaging/{feed_id}/nuget/v3/flat2/pkg/index.json"
    )

    assert response.status_code == 400
    assert "outside the configured feed" in response.text
    assert called is False


def test_azure_proxy_does_not_learn_feed_ids_from_non_service_index_json(
    tmp_path: Path,
) -> None:
    called_urls: list[str] = []
    feed_id = "33333333-3333-3333-3333-333333333333"

    def handler(request: httpx.Request) -> httpx.Response:
        called_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "packageContent": (
                            "https://pkgs.dev.azure.com/example/example/"
                            f"_packaging/{feed_id}/nuget/v3/flat2/pkg/index.json"
                        )
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["AZURE_ARTIFACTS_PAT"] = "test-pat"

    search_response = client.get("/nuget/azure/v3/query")
    assert search_response.status_code == 200

    response = client.get(
        f"/nuget/azure/example/example/_packaging/{feed_id}/nuget/v3/flat2/pkg/index.json"
    )

    assert response.status_code == 400
    assert "outside the configured feed" in response.text
    assert called_urls == [
        "https://pkgs.dev.azure.com/example/example/_packaging/example/nuget/v3/query"
    ]


def test_azure_proxy_learns_only_service_index_resource_urls(tmp_path: Path) -> None:
    called_urls: list[str] = []
    resource_feed_id = "44444444-4444-4444-4444-444444444444"
    unrelated_feed_id = "55555555-5555-5555-5555-555555555555"

    def handler(request: httpx.Request) -> httpx.Response:
        called_urls.append(str(request.url))
        if str(request.url).endswith("/_packaging/example/nuget/v3/index.json"):
            return httpx.Response(
                200,
                json={
                    "resources": [
                        {
                            "@id": (
                                "https://pkgs.dev.azure.com/example/example/"
                                f"_packaging/{resource_feed_id}/nuget/v3/flat2/"
                            ),
                            "@type": "PackageBaseAddress/3.0.0",
                        }
                    ],
                    "metadata": {
                        "diagnosticUrl": (
                            "https://pkgs.dev.azure.com/example/example/"
                            f"_packaging/{unrelated_feed_id}/nuget/v3/flat2/"
                        )
                    },
                },
            )
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["AZURE_ARTIFACTS_PAT"] = "test-pat"

    index_response = client.get("/nuget/azure/v3/index.json")
    assert index_response.status_code == 200

    allowed_response = client.get(
        f"/nuget/azure/example/example/_packaging/{resource_feed_id}/nuget/v3/flat2/pkg/index.json"
    )
    rejected_response = client.get(
        f"/nuget/azure/example/example/_packaging/{unrelated_feed_id}/nuget/v3/flat2/pkg/index.json"
    )

    assert allowed_response.status_code == 200
    assert rejected_response.status_code == 400
    assert "outside the configured feed" in rejected_response.text
    assert (
        called_urls[-1]
        == f"https://pkgs.dev.azure.com/example/example/_packaging/{resource_feed_id}/nuget/v3/flat2/pkg/index.json"
    )


def test_public_proxy_rewrites_index_urls(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "version": "3.0.0",
                "resources": [
                    {
                        "@id": "https://api.nuget.org/v3-flatcontainer/",
                        "@type": "PackageBaseAddress/3.0.0",
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    app = create_app(config, transport=transport)
    client = TestClient(app)

    response = client.get("/nuget/nuget-org/v3/index.json")

    assert response.status_code == 200
    payload = response.json()
    resource_url = payload["resources"][0]["@id"]
    assert resource_url.startswith("http://testserver/nuget/nuget-org")
    assert "api.nuget.org" not in resource_url


def test_nuget_relay_uses_root_directory_provider_overlay(tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    write_provider_overlay(
        config,
        {
            "nuget_azure_feed_base_url": "https://pkgs.dev.azure.com/root/root/_packaging/root/nuget",
            "nuget_azure_pat_env": "ROOT_AZURE_ARTIFACTS_PAT",
        },
    )
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["ROOT_AZURE_ARTIFACTS_PAT"] = "root-pat"

    response = client.get("/nuget/azure/v3/index.json")

    assert response.status_code == 200
    assert captured["authorization"].startswith("Basic ")
    assert (
        captured["url"]
        == "https://pkgs.dev.azure.com/root/root/_packaging/root/nuget/v3/index.json"
    )
