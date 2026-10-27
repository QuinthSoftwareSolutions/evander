"""Disposable process-death injector for the real-browser receipt regression."""

import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from evander_browser_sessions_addon.models import RunRequest
from evander_browser_sessions_addon.service import Parent, Service


class Bindings:
    def parent(self, agent, tool=None):
        return Parent(agent, "root/demo", time.time() + 600)

    def classifier(self, _):
        raise AssertionError("An explicit plan cannot acquire a decision provider")


runtime, session_id, phase = sys.argv[1:]
service = Service(Path(runtime), bindings=Bindings())
request = RunRequest.model_validate_json(sys.stdin.read())
if phase == "before_send":
    peer = service.relay_peer

    def relay(session):
        with service.store.connect() as db:
            pending = db.execute("SELECT 1 FROM commands WHERE run_id IS NOT NULL AND observing=0 AND state='submitted'").fetchone()
        if pending:
            os._exit(53)
        return peer(session)

    service.relay_peer = relay
else:
    finish = service.store.finish_command

    def finish_command(command_id, status, **kwargs):
        with service.store.connect() as db:
            command = db.execute("SELECT observing FROM commands WHERE id=?", (command_id,)).fetchone()
        if command and not command[0] and status == "completed":
            if phase == "after_commit":
                finish(command_id, status, **kwargs)
            os._exit(53)
        return finish(command_id, status, **kwargs)

    service.store.finish_command = finish_command

service.run("agent-a", session_id, request)
raise SystemExit("Crash injection point was not reached")
