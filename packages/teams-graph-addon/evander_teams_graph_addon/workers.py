"""Adapters for host-supervised integration workers and existing systemd entry points."""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Event


class PollingWorker:
    def __init__(self, worker):
        self.worker = worker

    def run(self, stop: Event) -> None:
        while not stop.is_set():
            result = self.worker.run_once_with_lock()
            stop.wait(max(0.0, result.next_sleep_seconds))


class NotificationWorker:
    def __init__(self, config, project_dir):
        from .messaging.ingest import build_teams_ingest_worker
        from .messaging.notifications import _validate_loopback_listener, create_app

        worker = build_teams_ingest_worker(config, project_dir=project_dir)
        self.host = worker.worker_config.change_notification_listener_host
        self.port = worker.worker_config.change_notification_listener_port
        _validate_loopback_listener(self.host)
        self.app = create_app(
            config,
            project_dir=project_dir,
            store=worker.store,
            notification_processor=worker.process_notification_queue_once,
        )

    def run(self, stop: Event) -> None:
        import uvicorn

        async def serve():
            server = uvicorn.Server(
                uvicorn.Config(
                    self.app,
                    host=self.host,
                    port=self.port,
                    timeout_graceful_shutdown=5,
                )
            )

            async def observe_stop():
                while not stop.is_set():
                    await asyncio.sleep(0.1)
                server.should_exit = True

            observer = asyncio.create_task(observe_stop())
            try:
                if not stop.is_set():
                    await server.serve()
            finally:
                observer.cancel()
                await asyncio.gather(observer, return_exceptions=True)

        asyncio.run(serve())


def create_worker(kind, config, project_dir):
    from evander.optional_integrations import require_teams_graph_module

    require_teams_graph_module("evander_teams_graph_addon", config)
    project_dir = Path(project_dir) if project_dir is not None else Path.cwd()
    if kind == "teams-notifications":
        return NotificationWorker(config, project_dir)
    if kind == "teams-ingest":
        from .messaging.ingest import build_teams_ingest_worker

        return PollingWorker(build_teams_ingest_worker(config, project_dir=project_dir))
    if kind == "teams-meeting-ingest":
        from .messaging.meeting_ingest import build_teams_meeting_ingest_worker

        return PollingWorker(build_teams_meeting_ingest_worker(config, project_dir=project_dir))
    raise ValueError("Unknown Teams worker")
