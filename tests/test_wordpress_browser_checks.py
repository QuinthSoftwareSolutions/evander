"""Opt-in real Chromium probes against a synthetic, pinned HTTPS fixture."""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import os
import shlex
import shutil
import ssl
import threading

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import hashes
import pytest

from evander.http_transport import _PinnedAddressAsyncHTTPTransport
from evander_wordpress_addon import checks as probes
from evander_wordpress_addon.models import Check, Site
from evander_wordpress_addon.state import Store

pytestmark = pytest.mark.skipif(
    os.environ.get("EVANDER_RUN_WORDPRESS_BROWSER_CHECKS") != "1",
    reason="requires a seeded Playwright module and Chromium",
)


def test_real_browser_probes_detect_confirmed_server_regression_and_capture_both_viewports(
    tmp_path, monkeypatch
):
    executable = (
        os.environ.get("PLAYWRIGHT_MCP_EXECUTABLE_PATH")
        or shutil.which("google-chrome")
        or shutil.which("chromium")
    )
    assert executable, "Seed Chromium before opting into browser checks"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "browser.example.test")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("browser.example.test")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "certificate.pem", tmp_path / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    fingerprint = base64.b64encode(
        sha256(
            key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        ).digest()
    ).decode()
    wrapper = tmp_path / "chromium-fixture"
    wrapper.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(executable)
        + " --ignore-certificate-errors-spki-list="
        + shlex.quote(fingerprint)
        + ' "$@"\n'
    )
    wrapper.chmod(0o700)
    monkeypatch.setenv("PLAYWRIGHT_MCP_EXECUTABLE_PATH", str(wrapper))
    requests = []
    state = {"broken": False}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(500 if state["broken"] and self.path == "/" else 200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"""<!doctype html><main><h1>Synthetic website</h1>
<a href="/contact/">Contact</a><form><input required name="email"><button>Send</button></form></main>
<script>new WebSocket(location.origin.replace('https:','wss:')+'/socket/')</script>""")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_path, key_path)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = "https://browser.example.test:" + str(server.server_port)

    async def fixture_transport(url):
        assert url == origin
        # Only the DNS boundary is substituted. Chromium, TLS, HTTP, selectors,
        # link navigation, WebSocket isolation and screenshots run for real.
        return _PinnedAddressAsyncHTTPTransport(
            allowed_host="browser.example.test",
            approved_addresses=[ipaddress.ip_address("127.0.0.1")],
        )

    monkeypatch.setattr(probes, "_public_transport", fixture_transport)
    store = Store(tmp_path / "runtime")
    checker = probes.BrowserChecks(store)
    site = Site(origin=origin, connector_id="fixture")
    specifications = [Check(selector="main", links=["/contact/"], form_selector="form")]

    async def exercise():
        before = await checker.run(site, specifications, "before")
        assert not before["failures"] and not before["uncertain"]
        assert {item["viewport"]["width"] for item in before["screenshots"]} == {390, 1280}
        for shot in before["screenshots"]:
            assert store.read_artifact(shot["artifact"]).startswith(b"\x89PNG\r\n\x1a\n")
        state["broken"] = True
        after = await checker.run(site, specifications, "after")
        confirmed = await checker.run(site, specifications, "confirm")
        assert not after["uncertain"] and not confirmed["uncertain"]
        assert probes.confirmed_regressions(before, after, confirmed) == [
            "/:1280:server",
            "/:390:server",
        ]
        assert "/contact/" in requests and "/socket/" not in requests

    try:
        asyncio.run(exercise())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
