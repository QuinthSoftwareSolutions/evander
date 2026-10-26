import asyncio
import threading

from evander_browser_sessions_addon.models import MessageRequest
from evander_browser_sessions_addon.relay import Broker, create_app
from evander_browser_sessions_addon.service import Service
from evander_browser_sessions_addon.state import Store


def test_message_wakes_idle_dispatcher_during_slow_reconciliation(domain, hosted, monkeypatch):
    from evander.orchestration.host_polling import HostWakeSleeper

    service, bindings, peers = domain
    reconciling, release_reconcile = threading.Event(), threading.Event()
    sleeping, delivered = threading.Event(), threading.Event()
    original_sleep = HostWakeSleeper.sleep

    def reconcile():
        reconciling.set()
        assert release_reconcile.wait(10)

    def sleep(self, seconds):
        sleeping.set()
        original_sleep(self, seconds)

    def enqueue(session, message):
        bindings.prompts.append(message["id"])
        delivered.set()
        return {"delivery_status": "started"}

    monkeypatch.setattr(service, "reconcile", reconcile)
    monkeypatch.setattr(HostWakeSleeper, "sleep", sleep)
    bindings.enqueue = enqueue
    # Independent service/store instances model the Admin Web and relay writers.
    writer = Service(service.runtime_dir, store=Store(service.runtime_dir),
                     bindings=bindings, peer_factory=peers)
    app = create_app(service.runtime_dir, service=service, internal_token="test-relay")

    async def exercise():
        async with app.router.lifespan_context(app):
            try:
                assert await asyncio.to_thread(reconciling.wait, 2)
                assert await asyncio.to_thread(sleeping.wait, 2)
                request = MessageRequest(request_id="wake", body="Continue the browser task")
                message = await asyncio.to_thread(writer.enqueue_message, hosted.id, "user:operator", request)
                assert await asyncio.to_thread(delivered.wait, 2), "Dispatch waited for the five-second poll"
                assert not release_reconcile.is_set(), "Reconciliation must not block delivery"
                await asyncio.to_thread(writer.enqueue_message, hosted.id, "user:operator", request)
                updates = service.store.message_updates(hosted.id)
                # The callback completes before the durable dispatch status is committed.
                for _ in range(100):
                    if updates[0]["delivery_status"] == "started":
                        break
                    await asyncio.sleep(0.01)
                    updates = service.store.message_updates(hosted.id)
                assert updates == [{"id": message["id"], "status": "dispatched", "delivery_status": "started"}]
            finally:
                release_reconcile.set()
        assert bindings.prompts == [message["id"]]
        assert not service.message_wake_path.exists()

    asyncio.run(exercise())


def test_startup_recovers_message_saved_without_a_wake_listener(domain, hosted):
    service, bindings, _ = domain
    message = service.enqueue_message(
        hosted.id, "user:operator", MessageRequest(request_id="offline", body="Resume sharing")
    )
    delivered = threading.Event()

    def enqueue(session, value):
        bindings.prompts.append(value["id"])
        delivered.set()
        return {"delivery_status": "started"}

    bindings.enqueue = enqueue
    app = create_app(service.runtime_dir, service=service, internal_token="test-relay")

    async def exercise():
        async with app.router.lifespan_context(app):
            assert await asyncio.to_thread(delivered.wait, 2)
        assert bindings.prompts == [message["id"]]

    asyncio.run(exercise())


def test_message_notifications_survive_frame_coalescing_and_cleanup(domain, hosted):
    service, _, _ = domain
    broker = Broker(service)

    async def exercise():
        notification = asyncio.Queue()
        frames = asyncio.Queue()
        accepted = asyncio.Event()

        class Socket:
            path_params = {"session_id": hosted.id}
            headers = {"authorization": "Bearer test-relay"}

            async def accept(self):
                accepted.set()

            async def send_json(self, payload):
                if payload["kind"] == "messages":
                    notification.put_nowait(payload)
                elif payload["kind"] == "frame":
                    frames.put_nowait(payload["image"])

        task = asyncio.create_task(broker.view(Socket(), "test-relay"))
        try:
            await asyncio.wait_for(accepted.wait(), 1)
            broker.messages_changed()
            for index in range(100):
                await broker.publish(hosted.id, {"kind": "frame", "image": str(index)})
            assert await asyncio.wait_for(notification.get(), 1) == {"kind": "messages"}
            assert await asyncio.wait_for(frames.get(), 1) == "98"
            assert await asyncio.wait_for(frames.get(), 1) == "99"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert not broker.watchers.get(hosted.id)
        assert not broker.message_watchers.get(hosted.id)

    asyncio.run(exercise())
