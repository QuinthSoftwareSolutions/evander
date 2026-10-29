import os

import httpx

from .models import FixtureError


class WorkerClient:
    def __init__(self, worker, *, client=None):
        self.worker = worker
        self.client = client

    def call(self, operation, lease_id=None, payload=None):
        token = os.environ.get(self.worker.token_env, "")
        if not token:
            raise FixtureError("Fixture worker authentication is not configured")
        path = "/healthz" if operation == "health" else "/v1/leases/" + lease_id + "/" + operation
        client = self.client or httpx.Client(
            timeout=httpx.Timeout(180, connect=5), follow_redirects=False, trust_env=False
        )
        try:
            response = client.request(
                "GET" if operation in {"health", "get"} else "POST",
                self.worker.fixture_base_url + path,
                headers={"Authorization": "Bearer " + token},
                json=payload,
            )
            if response.status_code >= 400:
                raise FixtureError(f"Fixture worker returned HTTP {response.status_code}")
            value = response.json()
            if not isinstance(value, dict):
                raise FixtureError("Invalid fixture worker response")
            return value
        except (httpx.HTTPError, ValueError):
            raise FixtureError("Fixture worker request failed") from None
        finally:
            if self.client is None:
                client.close()
