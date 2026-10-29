"""Run the actual Ansible acknowledgement tasks against a synthetic Nexus API."""

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

import pytest
import yaml


@pytest.mark.parametrize("opt_in, status", [(False, 204), (True, 204), (True, 403)])
def test_nexus_telemetry_requires_explicit_opt_in_and_successful_api(tmp_path, opt_in, status):
    executable = shutil.which("ansible-playbook")
    if executable is None:
        pytest.skip("Ansible is needed to exercise deployment tasks")
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            requests.append((self.path, self.headers.get("Authorization"), json.loads(body)))
            self.send_response(status)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    tasks = Path(__file__).resolve().parents[1] / "ansible/roles/package_cache_nexus/tasks/telemetry.yml"
    playbook = tmp_path / "playbook.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost", "gather_facts": False,
        "vars": {
            "evander_nexus_port": server.server_port,
            "evander_nexus_admin_user": "test-admin",
            "evander_nexus_admin_password": "test-password",
            "evander_nexus_acknowledge_mandatory_telemetry": opt_in,
        },
        "tasks": [{"ansible.builtin.include_tasks": str(tasks)}],
    }]))
    env = {**os.environ, "ANSIBLE_LOCAL_TEMP": str(tmp_path / "ansible-local"),
           "ANSIBLE_REMOTE_TEMP": str(tmp_path / "ansible-remote"), "ANSIBLE_NOCOLOR": "1"}
    try:
        result = subprocess.run(
            [executable, "-i", "localhost,", "-c", "local", str(playbook)],
            env=env, text=True, capture_output=True, timeout=45,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert (result.returncode == 0) is (not opt_in or status == 204), result.stdout + result.stderr
    if not opt_in:
        assert requests == []
    else:
        expected_auth = "Basic " + base64.b64encode(b"test-admin:test-password").decode()
        assert requests == [("/service/rest/v1/system/mandatory-telemetry-acknowledgement",
                             expected_auth, {"acknowledgement": True})]
