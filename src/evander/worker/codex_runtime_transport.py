"""Standalone stdlib adapter executed inside a scanner container.

This code, native Codex, and their entire environment are untrusted by the worker.
They receive no real subscription credentials. The worker's Unix HTTP relay owns
upstream routing and credential attachment independently of this TLS adapter.
"""

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import threading

ROOT = "/run/evander/codex-runtime"
MAX_BODY = 32 * 1024 * 1024


class UnixConnection(http.client.HTTPConnection):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.host)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        self.close_connection = True
        # Keep native OpenAI inference on the broker's HTTP streaming path.
        if self.headers.get("Upgrade"):
            self.send_error(426, "Use the HTTP streaming transport")
            return
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) > 1:
            self.send_error(400)
            return
        raw_length = lengths[0] if lengths else "0"
        if not raw_length.isdecimal() or int(raw_length) > MAX_BODY:
            self.send_error(413)
            return
        self.connection.settimeout(30)
        body = self.rfile.read(int(raw_length))
        if len(body) != int(raw_length):
            return
        started = False
        try:
            with UnixConnection(ROOT + "/models.sock", timeout=120) as connection:
                headers = {
                    key: value
                    for key, value in self.headers.items()
                    if key.lower() not in {"host", "connection", "content-length"}
                }
                headers.setdefault("version", self.server.native_version)
                connection.request(self.command, self.path, body=body, headers=headers)
                response = connection.getresponse()
                self.send_response_only(response.status)
                for key, value in response.getheaders():
                    if key.lower() not in {"connection", "transfer-encoding", "content-length"}:
                        self.send_header(key, value)
                if response.status not in {204, 304}:
                    self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Connection", "close")
                self.end_headers()
                started = True
                if response.status in {204, 304}:
                    return
                while chunk := response.read1(65536):
                    self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
        except (OSError, http.client.HTTPException):
            if not started:
                self.send_error(502, "Worker model relay unavailable")


def main():
    command = sys.argv[1:]
    if command[1:] == ["--version"]:
        return subprocess.call(command)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    version = subprocess.run(
        [command[0], "--version"], capture_output=True, text=True, check=True, timeout=10
    )
    server.native_version = version.stdout.strip().removeprefix("codex-cli ")
    server.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(ROOT + "/policy/loopback-cert.pem", ROOT + "/policy/loopback-key.pem")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"https://127.0.0.1:{server.server_port}/backend-api"
    command += [
        "--config",
        f'chatgpt_base_url="{base}"',
        "--config",
        f'openai_base_url="{base}/codex"',
    ]
    environment = dict(os.environ)
    environment["CODEX_CA_CERTIFICATE"] = ROOT + "/policy/loopback-ca.pem"
    environment["NO_PROXY"] = "127.0.0.1,localhost"
    environment.pop("SSL_CERT_FILE", None)
    try:
        return subprocess.call(command, env=environment)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())


def transport_source():
    return Path(__file__).read_text()
