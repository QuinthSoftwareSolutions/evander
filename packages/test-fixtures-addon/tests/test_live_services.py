"""Opt-in protocol smoke, executed inside an already-authorized disposable job.

No provisioning or remote mutation is performed by collecting this test.
Set EVANDER_TEST_FIXTURE_CONFIG to that job's private combined fixture JSON.
"""

import json
import os
from pathlib import Path
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("EVANDER_TEST_FIXTURE_CONFIG"),
    reason="requires explicitly allocated disposable job fixtures",
)


def test_real_sql_source_target_and_rabbitmq_round_trip():
    import pika
    import pyodbc

    config = json.loads(Path(os.environ["EVANDER_TEST_FIXTURE_CONFIG"]).read_text())
    table = "fixture_" + uuid.uuid4().hex
    for alias in ("source", "target"):
        database = config[alias]
        # Assertions avoid including secrets even when a connection fails.
        try:
            connection = pyodbc.connect(
                "DRIVER={ODBC Driver 18 for SQL Server};"
                f"SERVER=tcp:{database['server']},{database['port']};"
                f"DATABASE={database['database_name']};UID={database['username']};"
                f"PWD={database['password']};Encrypt=yes;TrustServerCertificate=yes",
                timeout=10,
            )
        except Exception:
            pytest.fail(f"{alias} SQL connection failed", pytrace=False)
        try:
            cursor = connection.cursor()
            cursor.execute(f"CREATE TABLE [{table}] (value int NOT NULL)")
            cursor.execute(f"INSERT INTO [{table}] VALUES (42)")
            assert cursor.execute(f"SELECT value FROM [{table}]").fetchone()[0] == 42
            connection.rollback()
        finally:
            connection.close()
    broker = config["rabbitmq"]
    try:
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=broker["host"],
                port=broker["port"],
                virtual_host=broker["vhost"],
                credentials=pika.PlainCredentials(broker["username"], broker["password"]),
                socket_timeout=10,
                blocked_connection_timeout=10,
                connection_attempts=1,
            )
        )
    except Exception:
        pytest.fail("RabbitMQ connection failed", pytrace=False)
    try:
        channel = connection.channel()
        queue = channel.queue_declare(queue="", exclusive=True).method.queue
        channel.basic_publish(exchange="", routing_key=queue, body=b"fixture protocol smoke")
        method, _, body = channel.basic_get(queue=queue, auto_ack=True)
        assert method is not None and body == b"fixture protocol smoke"
    finally:
        connection.close()
