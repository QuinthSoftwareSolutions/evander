"""Runs entirely inside the opt-in test's isolated network/filesystem namespace."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import threading


calls = []


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        # Exercise native OpenAI's HTTP fallback without providing a WebSocket service.
        self.send_error(426)

    def do_POST(self):
        content = self.rfile.read(int(self.headers["Content-Length"]))
        if self.headers.get("Content-Encoding") == "zstd":
            content = subprocess.run(
                ["zstd", "-dq"], input=content, capture_output=True, check=True, timeout=5
            ).stdout
        body = json.loads(content)
        calls.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        item = {
            "id": "msg_local",
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "content": [{"type": "output_text", "text": "local relay works"}],
        }
        events = [
            {"type": "response.created", "response": {"id": "resp_local"}},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_local",
                    "status": "completed",
                    "output": [item],
                    "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                },
            },
        ]
        data = b"".join(("data: " + json.dumps(event) + "\n\n").encode() for event in events)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
inputs = json.loads(Path("/tmp/inputs.json").read_text())
home = Path("/home/agent")
codex_home = home / (".config/evander/codex" if inputs["backend"] == "container" else ".codex")
codex_home.mkdir(parents=True)
base = f"http://127.0.0.1:{server.server_port}/v1/session/synthetic/auth/scoped-token"
config_path = codex_home / "config.toml"
config_path.write_text(inputs["config"].replace("http://relay.invalid", base))
environment = {
    **os.environ,
    "PATH": "/tmp/bin:/usr/bin:/bin",
    "HOME": str(home),
    "OPENAI_API_KEY": "scoped-token",
    "CODEX_API_KEY": "stale-key",
    # The scoped configured URL must win over a generic environment override.
    "OPENAI_BASE_URL": "http://127.0.0.1:1/wrong-session",
}
if inputs["backend"] == "container":
    environment["CODEX_HOME"] = str(codex_home)
outputs = []
thread_id = None
try:
    for command in inputs["commands"]:
        result = subprocess.run(
            ["bash", "-c", command.replace("__THREAD_ID__", thread_id or "")],
            env=environment,
            cwd=home,
            text=True,
            capture_output=True,
            timeout=25,
        )
        outputs.append(
            {"code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        )
        match = re.search(r"session id: ([\w-]+)", result.stdout)
        if match:
            thread_id = match[1]
        if result.returncode or len(calls) != len(outputs):
            break
    # Missing/malformed relay configuration must fail before launching a model turn.
    for content in ("", '[model_providers.evander_openai_relay]\nbase_url="file:///private"\n'):
        config_path.write_text(content)
        result = subprocess.run(
            ["bash", "-c", inputs["commands"][1].replace("__THREAD_ID__", thread_id or "")],
            env=environment,
            cwd=home,
            text=True,
            capture_output=True,
            timeout=10,
        )
        outputs.append(
            {"code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        )
    print(json.dumps({"calls": calls, "outputs": outputs, "thread_id": thread_id}))
finally:
    server.shutdown()
    server.server_close()
