"""Opt-in native adapter regression using an already seeded RabbitMQ image.

Set EVANDER_TEST_FIXTURES_RABBITMQ_IMAGE to an approved repository digest.
Requires local Docker, IPv4 iptables and host access to internal bridge addresses.
Only randomly named, labelled test resources are created; no image is pulled.
"""

import os
import secrets
import time
import uuid

import pytest

from evander_test_fixtures_addon.docker import Docker
from evander_test_fixtures_addon.models import NotReady, WorkerSettings

pytestmark = pytest.mark.skipif(
    not os.environ.get("EVANDER_TEST_FIXTURES_RABBITMQ_IMAGE"),
    reason="requires an explicitly selected, pre-seeded local RabbitMQ image",
)


def ready(docker, lease):
    deadline = time.monotonic() + 90
    while True:
        try:
            return docker.ensure_broker(lease)
        except NotReady:
            if time.monotonic() >= deadline:
                pytest.fail("RabbitMQ did not become ready", pytrace=False)
            time.sleep(1)


def connect(pika, host, lease):
    return pika.BlockingConnection(
        pika.ConnectionParameters(
            host=host,
            virtual_host=lease["lease_id"],
            credentials=pika.PlainCredentials("fixture", lease["secrets"]["rabbitmq_password"]),
            socket_timeout=1,
            stack_timeout=3,
            blocked_connection_timeout=1,
            connection_attempts=1,
        )
    )


def round_trip(pika, host, lease):
    try:
        connection = connect(pika, host, lease)
    except Exception:
        pytest.fail("RabbitMQ connection failed", pytrace=False)
    try:
        channel = connection.channel()
        channel.queue_declare(queue="round-trip", durable=True)
        channel.basic_publish(exchange="", routing_key="round-trip", body=b"fixture smoke")
        method, _, body = channel.basic_get(queue="round-trip", auto_ack=True)
        assert method is not None and body == b"fixture smoke"
    finally:
        if connection.is_open:
            connection.close()


def test_native_broker_limits_fault_recovery_reset_and_cleanup(tmp_path):
    """Catch CLI incompatibility before readiness and verify actual broker limits."""
    pika = pytest.importorskip("pika")
    from pika.adapters.utils.connection_workflow import AMQPConnectorException

    settings = WorkerSettings(
        worker_id="synthetic-worker",
        rabbitmq_image=os.environ["EVANDER_TEST_FIXTURES_RABBITMQ_IMAGE"],
    )
    docker = Docker(tmp_path, settings.rabbitmq_image)
    lease = {
        "lease_id": "fx-" + uuid.uuid4().hex,
        "profile": "rabbitmq",
        "secrets": {"rabbitmq_password": secrets.token_urlsafe(32)},
        "resource_counts": {},
    }
    try:
        host = ready(docker, lease)
        container_id = docker.inspect(lease["lease_id"])["Id"]
        assert ready(docker, lease) == host
        assert docker.inspect(lease["lease_id"])["Id"] == container_id
        round_trip(pika, host, lease)

        connection = connect(pika, host, lease)
        try:
            channel = connection.channel()
            # The round-trip queue already occupies one of the 32 allowed slots.
            for index in range(31):
                channel.queue_declare(queue=f"bounded-{index}", durable=True)
            with pytest.raises(pika.exceptions.ChannelClosedByBroker):
                channel.queue_declare(queue="over-limit", durable=True)
        finally:
            if connection.is_open:
                connection.close()

        for action in ("pause", "stop"):
            docker.control(lease, action)
            state = docker.inspect(lease["lease_id"])["State"]
            assert state["Paused"] if action == "pause" else not state["Running"]
            try:
                unexpected = connect(pika, host, lease)
            except (pika.exceptions.AMQPError, AMQPConnectorException, OSError):
                pass
            else:
                unexpected.close()
                pytest.fail("Broker stayed available during a requested fault", pytrace=False)
            docker.recover(lease)
            host = ready(docker, lease)
            round_trip(pika, host, lease)

        docker.remove_broker(lease)
        host = ready(docker, lease)
        assert docker.inspect(lease["lease_id"])["Id"] != container_id
        connection = connect(pika, host, lease)
        try:
            with pytest.raises(pika.exceptions.ChannelClosedByBroker) as error:
                connection.channel().queue_declare(queue="round-trip", passive=True)
            assert error.value.reply_code == 404
        finally:
            if connection.is_open:
                connection.close()
        round_trip(pika, host, lease)
    finally:
        docker.remove_broker(lease)

    # A repeat cleanup must also succeed after all three native resources are gone.
    docker.remove_broker(lease)
    for kind in ("container", "network", "volume"):
        assert docker.inspect(lease["lease_id"], kind) is None
    assert not (tmp_path / lease["lease_id"]).exists()
