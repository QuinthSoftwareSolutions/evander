#!/usr/bin/env python3
"""Exercise real MCP/Admin ASGI requests and durable workflow writes in temporary state.

No model, guest, external service or deployed runtime is contacted. Use the same
script with each checkout's PYTHONPATH for comparable before/after measurements.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import UTC, datetime, timedelta
import functools
import json
import logging
import os
from pathlib import Path
import sqlite3
import statistics
import tempfile
import threading
import time

import anyio.to_thread
import httpx

from evander.addons import AddonLoaderConfig
from evander.admin.composition import build_admin_runtime
from evander.admin.web.app import create_app as admin_app
from evander.events.models import TriggerEvent
from evander.gateway.app import create_app as gateway_app
from evander.host_config import HostConfig, save_host_config
from evander.models import SessionManifest, ToolName
from evander.session_auth import ensure_session_mcp_auth_token
from evander.triggers.queue import SqliteTriggerEventQueue


def distribution(values):
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "p50_ms": round(statistics.median(ordered) * 1000, 3) if ordered else 0,
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] * 1000, 3)
        if ordered
        else 0,
        "max_ms": round(max(ordered, default=0) * 1000, 3),
    }


class Measurements:
    def __init__(self):
        self.samples = defaultdict(list)
        self.lock = threading.Lock()
        self.active = self.peak_active = self.pending = self.peak_pending = 0
        self.busy_errors = 0

    def record(self, name, started):
        with self.lock:
            self.samples[name].append(time.perf_counter() - started)

    def install(self):
        original_run = anyio.to_thread.run_sync
        original_connect = sqlite3.connect
        measurements = self

        async def measured_run(function, *args, **kwargs):
            submitted = time.perf_counter()
            with self.lock:
                self.pending += 1
                self.peak_pending = max(self.pending, self.peak_pending)
            began = False

            def run():
                nonlocal began
                began = True
                self.record("worker_queue", submitted)
                started = time.perf_counter()
                with self.lock:
                    self.pending -= 1
                    self.active += 1
                    self.peak_active = max(self.active, self.peak_active)
                try:
                    return function(*args)
                finally:
                    self.record("worker_execution", started)
                    with self.lock:
                        self.active -= 1

            try:
                return await original_run(run, **kwargs)
            finally:
                if not began:
                    with self.lock:
                        self.pending -= 1

        class Connection(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                started = time.perf_counter()
                try:
                    return super().execute(sql, *args, **kwargs)
                except sqlite3.OperationalError as exc:
                    if getattr(exc, "sqlite_errorcode", 0) & 255 in (5, 6):
                        with measurements.lock:
                            measurements.busy_errors += 1
                    raise
                finally:
                    measurements.record("sqlite_execute", started)
                    if sql.strip().upper().startswith("BEGIN"):
                        measurements.record("sqlite_begin", started)

        def connect(*args, **kwargs):
            kwargs.setdefault("factory", Connection)
            return original_connect(*args, **kwargs)

        anyio.to_thread.run_sync = measured_run
        sqlite3.connect = connect
        return lambda: self.restore(original_run, original_connect)

    @staticmethod
    def restore(run, connect):
        anyio.to_thread.run_sync = run
        sqlite3.connect = connect


def rpc_payload(response):
    response.raise_for_status()
    if response.headers.get("content-type", "").startswith("application/json"):
        payload = response.json()
    else:
        payload = json.loads(
            next(line[6:] for line in response.text.splitlines() if line.startswith("data: "))
        )
    assert "error" not in payload, payload
    assert not payload.get("result", {}).get("isError"), payload
    return payload


async def lock_probe(root):
    """Measure an actual SQLite writer wait separately from the mixed workload."""
    runtime_dir = root / "runtime"
    save_host_config(
        HostConfig(
            runtime_dir=runtime_dir, addon_loader=AddonLoaderConfig(enabled_entry_points=False)
        )
    )
    app = admin_app(build_admin_runtime(runtime_dir))
    measurements = Measurements()
    held = threading.Event()
    begin_attempted = threading.Event()
    restore = measurements.install()
    original_connect = sqlite3.connect

    class LoginConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql.strip().upper() == "BEGIN IMMEDIATE":
                begin_attempted.set()
                started = time.perf_counter()
                try:
                    return super().execute(sql, *args, **kwargs)
                finally:
                    measurements.record("sqlite_writer_lock_wait", started)
            return super().execute(sql, *args, **kwargs)

    def connect(*args, **kwargs):
        kwargs["factory"] = LoginConnection
        return original_connect(*args, **kwargs)

    def hold_writer():
        # This thread owns its connection. It releases independently of the
        # request event loop, including on the old blocking implementation.
        with original_connect(runtime_dir / "admin-web.db") as connection:
            connection.execute("BEGIN IMMEDIATE")
            held.set()
            if not begin_attempted.wait(5):
                return
            time.sleep(0.2)

    writer = threading.Thread(target=hold_writer)
    writer.start()
    while not held.is_set():
        await asyncio.sleep(0.001)
    sqlite3.connect = connect
    stop = asyncio.Event()

    async def heartbeat():
        while not stop.is_set():
            started = time.perf_counter()
            await asyncio.sleep(0.005)
            measurements.samples["event_loop_lag"].append(
                max(0, time.perf_counter() - started - 0.005)
            )

    ticker = asyncio.create_task(heartbeat())
    try:
        await asyncio.sleep(0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://admin.test"
        ) as client:
            response = await client.post(
                "/api/auth/login",
                json={"username": "benchmark-admin", "password": "benchmark-password"},
            )
            response.raise_for_status()
            assert response.json()["authenticated"]
    finally:
        stop.set()
        await ticker
        await asyncio.to_thread(writer.join, 5)
        restore()
    assert begin_attempted.is_set(), "Login did not attempt the expected SQLite write"
    assert measurements.samples["sqlite_writer_lock_wait"]
    return {
        "lock_held_after_writer_attempt_ms": 200,
        "timings": {key: distribution(value) for key, value in measurements.samples.items()},
    }


async def exercise(root, concurrency, rounds, history):
    runtime_dir = root / "runtime"
    save_host_config(
        HostConfig(
            runtime_dir=runtime_dir, addon_loader=AddonLoaderConfig(enabled_entry_points=False)
        )
    )
    config_dir = root / "config" / "trigger-orchestrator"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "runtime_dir": str(runtime_dir),
                "triggers": [],
                "gates": [],
                "effects": [],
                "queries": [],
                "edges": [],
            }
        )
    )
    service = build_admin_runtime(runtime_dir)
    context = service.runtime
    sessions = {}
    proofs = {}
    for index in range(history):
        manifest = SessionManifest(
            session_id=f"bench-{index}",
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        manifest, token = ensure_session_mcp_auth_token(manifest)
        sessions[manifest.session_id] = manifest
        proofs[manifest.session_id] = token
    context.session_repository.write_all(sessions)
    approvals = [
        context.policy.create_approval(f"bench-{index}", "benchmark", {}, "Benchmark")
        for index in range(concurrency)
    ]
    gateway = gateway_app(context)
    admin = admin_app(service)
    auth = admin.state.auth
    admin.state.admin_user_store.upsert_user(login="benchmark-operator")
    admin.state.admin_user_store.set_local_password(
        login="benchmark-operator", password="benchmark-password"
    )
    cookie = auth.login("benchmark-operator", "benchmark-password")
    queue = SqliteTriggerEventQueue(runtime_dir / "orchestration.db")
    headers = {"accept": "application/json, text/event-stream"}
    measurements = Measurements()

    async with gateway.router.lifespan_context(gateway):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway),
            base_url="http://gateway.test",
            headers=headers,
        ) as mcp:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=admin),
                base_url="http://admin.test",
                cookies={auth.config.cookie_name: cookie},
            ) as web:
                transports = []
                for index in range(concurrency):
                    session_id = f"bench-{index}"
                    identity = {
                        "x-evander-session-id": session_id,
                        "x-evander-mcp-session-token": proofs[session_id],
                    }
                    response = await mcp.post(
                        "/mcp",
                        headers=identity,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": "2024-11-05",
                                "capabilities": {},
                                "clientInfo": {"name": "benchmark", "version": "1"},
                            },
                        },
                    )
                    rpc_payload(response)
                    identity["mcp-session-id"] = response.headers["mcp-session-id"]
                    transports.append(identity)
                restore = measurements.install()
                stop = asyncio.Event()

                async def heartbeat():
                    while not stop.is_set():
                        started = time.perf_counter()
                        await asyncio.sleep(0.005)
                        measurements.samples["event_loop_lag"].append(
                            max(0, time.perf_counter() - started - 0.005)
                        )

                async def timed(name, operation):
                    started = time.perf_counter()
                    result = await operation
                    measurements.record(name, started)
                    return result

                def workflow(index, turn):
                    event = TriggerEvent(
                        event_id=f"{index}-{turn}",
                        trigger_id="benchmark",
                        trigger_type="cron",
                        occurred_at=datetime.now(UTC),
                    )
                    queue.publish(event)
                    assert queue.mark_delivered(event.event_id)

                async def session(index):
                    for turn in range(rounds):
                        method = "tools/list" if turn % 3 == 0 else "tools/call"
                        request = {"jsonrpc": "2.0", "id": turn + 2, "method": method}
                        if method == "tools/call":
                            request["params"] = {
                                "name": "approval_check",
                                "arguments": {"request_id": approvals[index].request_id},
                            }
                        response = await timed(
                            method, mcp.post("/mcp", headers=transports[index], json=request)
                        )
                        rpc_payload(response)
                        response = await timed("admin_poll", web.get("/api/auth/session"))
                        response.raise_for_status()
                        await timed(
                            "workflow_write",
                            anyio.to_thread.run_sync(functools.partial(workflow, index, turn)),
                        )
                        response = await timed(
                            "workflow_poll", web.get("/api/workflow-editor/runs")
                        )
                        response.raise_for_status()

                ticker = asyncio.create_task(heartbeat())
                started = time.perf_counter()
                try:
                    await asyncio.gather(*(session(index) for index in range(concurrency)))
                finally:
                    stop.set()
                    await ticker
                    restore()
                duration = time.perf_counter() - started
    return {
        "concurrency": concurrency,
        "rounds": rounds,
        "history": history,
        "seconds": round(duration, 3),
        "completed_sessions": concurrency,
        "completed_workflow_writes": concurrency * rounds,
        "peak_workers": measurements.peak_active,
        "peak_queued": measurements.peak_pending,
        "sqlite_busy_errors": measurements.busy_errors,
        "timings": {key: distribution(value) for key, value in measurements.samples.items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[10, 50, 100])
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--history", type=int, default=1000)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lock-probe", action="store_true")
    args = parser.parse_args()
    if args.rounds < 1 or args.repeat < 1:
        parser.error("rounds and repeat must be positive")
    if min(args.concurrency) < 1 or args.history < max(args.concurrency):
        parser.error("history must cover every concurrent session")
    os.environ.update(
        EVANDER_ADMIN_WEB_AUTH_MODE="local_password",
        EVANDER_ADMIN_WEB_USERNAME="benchmark-admin",
        EVANDER_ADMIN_WEB_PASSWORD="benchmark-password",
        EVANDER_ADMIN_WEB_SESSION_SECRET="synthetic-benchmark-secret",
    )
    logging.disable(logging.CRITICAL)
    results = []
    for repeat in range(args.repeat):
        if args.lock_probe:
            with tempfile.TemporaryDirectory(prefix="evander-lock-benchmark-") as directory:
                result = asyncio.run(lock_probe(Path(directory)))
            result["repeat"] = repeat + 1
            results.append(result)
            args.output.write_text(json.dumps(results, indent=2) + "\n")
            print(json.dumps(result), flush=True)
            continue
        for concurrency in args.concurrency:
            with tempfile.TemporaryDirectory(prefix="evander-request-benchmark-") as directory:
                result = asyncio.run(
                    exercise(Path(directory), concurrency, args.rounds, args.history)
                )
            result["repeat"] = repeat + 1
            results.append(result)
            args.output.write_text(json.dumps(results, indent=2) + "\n")
            print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
