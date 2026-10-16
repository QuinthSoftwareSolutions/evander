import json
from pathlib import Path
import sys

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evander_rabbitmq_addon.adapter import Adapter
from evander.resources.models import Profile, ResourceError


class Credentials:
    def __init__(self):
        self.purposes = []

    def resolve(self, connection_id, raw, purpose, directory):
        self.purposes.append(purpose)
        return {"username": purpose, "password": "synthetic-secret"}


def connection():
    return {
        "management_url": "https://mq.example.invalid",
        "host": "mq.example.invalid",
        "allowed_vhosts": ["/application"],
    }


def test_message_get_is_post_with_explicit_ack_and_write_credentials():
    captured = []

    def handle(request):
        captured.append(request)
        return httpx.Response(200, json=[{"payload": "hello"}])

    adapter = Adapter(transport=httpx.MockTransport(handle))
    credentials = Credentials()
    result = adapter.call(
        "local",
        connection(),
        "get_messages",
        {"vhost": "/application", "name": "queue", "requeue": False},
        "root",
        credentials,
        None,
        "request",
    )
    assert result["messages"][0]["payload"] == "hello"
    request = captured[0]
    assert request.method == "POST" and "%2Fapplication" in str(request.url)
    assert json.loads(request.content)["ackmode"] == "ack_requeue_false"
    assert credentials.purposes == ["write"]
    assert "get_messages" not in adapter.read_operations


def test_foreign_vhost_never_reaches_provider():
    adapter = Adapter(
        transport=httpx.MockTransport(lambda req: pytest.fail("Provider must not be called"))
    )
    with pytest.raises(ResourceError, match="outside"):
        adapter.call(
            "local",
            connection(),
            "publish",
            {"vhost": "foreign", "payload": "x"},
            "root",
            Credentials(),
            None,
            "r",
        )


def test_shared_broker_fault_controls_are_rejected():
    profile = Profile(connection_id="local", options={"mode": "shared"})
    with pytest.raises(ResourceError, match="isolated"):
        Adapter().validate_control(profile, {"action": "stop", "duration_seconds": 1})


def test_large_provider_response_is_bounded_before_decoding():
    adapter = Adapter(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"x" * 2048))
    )
    with pytest.raises(ResourceError, match="limit"):
        adapter.http(
            {**connection(), "max_bytes": 1024},
            {"username": "u", "password": "p"},
            "GET",
            "health/checks/alarms",
        )


def test_publish_batch_stops_on_uncertainty_and_is_never_replayed_after_reload(tmp_path):
    from evander.addons import AddonContext
    from evander.resources.models import Owner, Settings
    from evander.resources.service import ResourceService
    from evander.resources.state import Store
    from types import SimpleNamespace

    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 2:
            raise httpx.ReadTimeout("private provider diagnostic", request=request)
        return httpx.Response(200, json={"routed": True})

    raw = {**connection(), "operations": ["publish"]}
    context = AddonContext("rabbitmq-addon", tmp_path, tmp_path / "addons/rabbitmq-addon")
    domain = ResourceService(
        context,
        Adapter(transport=httpx.MockTransport(handle)),
        credentials=Credentials(),
        authority=SimpleNamespace(owns=lambda _: True),
    )
    domain.store.save_settings("root", 0, Settings(connections={"approved": raw}), "operator")
    arguments = {
        "vhost": "/application",
        "messages": [
            {"routing_key": "queue", "payload": value} for value in ("one", "two", "three")
        ],
    }
    owner = Owner(key="session", directory="root")
    result = domain.call(owner, "approved", "publish", arguments, "batch-once")
    assert result["state"] == "uncertain"
    assert result["progress"] == {"acknowledged": 1, "uncertain": 1, "unattempted": 1}
    assert len(requests) == 2 and "private provider diagnostic" not in json.dumps(result)
    assert all(request.method == "POST" for request in requests)
    domain.store = Store(domain.store.root)
    assert domain.call(owner, "approved", "publish", arguments, "batch-once") == result
    assert len(requests) == 2
    with pytest.raises(ResourceError, match="limit"):
        domain.adapter.validate_call("publish", {"messages": [{"payload": "x" * 40000}] * 2})
