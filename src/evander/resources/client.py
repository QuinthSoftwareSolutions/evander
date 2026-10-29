import os
from urllib.parse import quote

import httpx

from .models import ResourceError, Uncertain


class WorkerClient:
    def __init__(self, worker):
        self.worker = worker

    def call(self, lease_id, operation, payload=None):
        return self.request(f"/v1/leases/{lease_id}/{operation}", payload)

    def health(self):
        return self.request("/healthz")

    def readiness(self, connection_id, directory):
        return self.request(
            "/v1/connections/"
            + quote(connection_id, safe="")
            + "/readiness?directory="
            + quote(directory, safe="")
        )

    def request(self, path, payload=None):
        token = os.environ.get(self.worker.token_env)
        if not token:
            raise ResourceError("Worker authentication is not configured")
        try:
            with httpx.Client(
                timeout=httpx.Timeout(45, connect=5), follow_redirects=False, trust_env=False
            ) as client:
                response = client.request(
                    "GET" if payload is None else "POST",
                    self.worker.service_url + path,
                    json=payload,
                    headers={"Authorization": "Bearer " + token},
                )
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError):
            raise Uncertain(
                "Worker result is unknown; inspect lease state before retrying"
            ) from None
