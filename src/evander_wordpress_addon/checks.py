"""Bounded browser probes and baseline-aware objective failure classification."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from evander.website_sessions import _public_transport

from .models import WordPressError


def confirmed_regressions(baseline, first, second):
    old = {x["id"] for x in baseline.get("failures", [])}
    left = {x["id"] for x in first.get("failures", []) if x.get("objective")}
    right = {x["id"] for x in second.get("failures", []) if x.get("objective")}
    return sorted((left & right) - old)


class BrowserChecks:
    def __init__(self, store, *, module_path=None):
        self.store = store
        self.module_path = module_path or os.environ.get("EVANDER_WORDPRESS_PLAYWRIGHT_MODULE")

    async def run(self, site, checks, label):
        # Validate public DNS before launch and pin the browser's hostname to an approved IP.
        origins = sorted(set([site.origin, *site.browser_origins]))
        hosts = {}
        for origin in origins:
            transport = await _public_transport(origin)
            hosts[urlsplit(origin).hostname] = str(transport.pinned_backend.approved_addresses[0])
            await transport.aclose()
        path = Path(__file__).with_name("checks.mjs")
        module = self.module_path or str(
            Path(__file__).parents[2] / "frontend/admin-web/node_modules/playwright/index.mjs"
        )
        payload = {
            "origin": site.origin,
            "checks": [c.model_dump() for c in checks],
            "hosts": hosts,
            "origins": origins,
            "module": module,
        }
        process = await asyncio.create_subprocess_exec(
            "node",
            str(path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(json.dumps(payload).encode()), 120
            )
        except BaseException:
            process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise WordPressError(
                "Browser checks are unavailable; install the operator Playwright runtime"
            )
        result = json.loads(stdout)
        import base64

        result["screenshots"] = [
            {
                "path": shot["path"],
                "viewport": shot["viewport"],
                "phase": label,
                "artifact": self.store.artifact(base64.b64decode(shot["data"]), "png"),
            }
            for shot in result.pop("images", [])
        ]
        return result
