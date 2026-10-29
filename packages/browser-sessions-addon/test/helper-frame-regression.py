"""Exercise delayed native helper frames over its real framed and CDP transports."""
import asyncio
import base64
import json
import io
from PIL import Image
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import time

import websockets
from websockets.exceptions import ConnectionClosed


async def regression(source):
    with tempfile.TemporaryDirectory(prefix="evander-helper-frames-") as folder:
        root = Path(folder)
        host = root / "helper"
        shutil.copytree(source, host)
        profile = root / "profile"
        profile.mkdir()
        approved = "http://approved.example/"
        private = "http://private.example/secret"
        selected = "selected-page"
        extension = "a" * 32
        socket = None
        hold = False
        pending_fence = None
        acknowledgements = set()
        capture_sizes = []

        async def peer(connection):
            nonlocal socket, pending_fence
            socket = connection
            try:
                async for raw in connection:
                    command = json.loads(raw)
                    result = {}
                    if command["method"] == "Target.getTargetInfo":
                        result = {"targetInfo": {"type": "page", "url": approved}}
                    elif command["method"] == "Target.attachToTarget":
                        result = {"sessionId": selected}
                    elif command["method"] == "Page.getFrameTree":
                        if hold and pending_fence is None:
                            pending_fence = command
                            continue
                        result = {"frameTree": {"frame": {"id": "main", "url": approved}}}
                    elif command["method"] == "Page.screencastFrameAck":
                        acknowledgements.add(command["params"]["sessionId"])
                    elif command["method"] == "Page.startScreencast":
                        capture_sizes.append(command["params"]["maxWidth"])
                    await connection.send(json.dumps({"id": command["id"], "result": result}))
            except ConnectionClosed:
                pass

        async with websockets.serve(peer, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            (profile / "DevToolsActivePort").write_text(f"{port}\n/devtools/browser/fixture\n")
            (host / "helper.json").write_text(json.dumps({"browser_data_dir": str(profile), "allowed_origins": [f"chrome-extension://{extension}/"]}))
            process = await asyncio.create_subprocess_exec(
                str(host / "evander-browser-helper"), f"chrome-extension://{extension}/",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            async def send(message):
                data = json.dumps(message).encode()
                process.stdin.write(struct.pack("<I", len(data)) + data)
                await process.stdin.drain()

            async def read():
                length = struct.unpack("<I", await process.stdout.readexactly(4))[0]
                return json.loads(await process.stdout.readexactly(length))

            async def event(method, params):
                await socket.send(json.dumps({"sessionId": selected, "method": method, "params": params}))

            async def frame(number, pixels):
                await event("Page.screencastFrame", {"sessionId": number, "data": base64.b64encode(pixels).decode()})

            async def navigate(url):
                await event("Page.frameNavigated", {"frame": {"id": "main", "url": url}})

            async def wait_for(check):
                async with asyncio.timeout(3):
                    while not check():
                        await asyncio.sleep(0.01)

            async def resume():
                nonlocal hold
                hold = False
                await socket.send(json.dumps({"id": pending_fence["id"], "result": {"frameTree": {"frame": {"id": "main", "url": approved}}}}))

            try:
                await send({"id": 1, "kind": "open", "target_id": "a" * 32, "state": {
                    "id": "frame-fixture", "state": "paused", "agent_session_id": "agent",
                    "allowed_origins": [approved.rstrip("/")], "expires_at": time.time() + 30,
                    "controller": None, "control_generation": 0,
                }})
                opened = await asyncio.wait_for(read(), 3)
                assert opened["ok"], opened
                hold = True
                await frame(1, b"older-approved-frame")
                await wait_for(lambda: pending_fence is not None)
                await navigate(private)
                await frame(2, b"private-pixels")
                await navigate(approved)
                # A separate native ping confirms both navigation events have
                # reached the helper while its first frame is blocked in CDP.
                async with asyncio.timeout(3):
                    sequence = 2
                    while True:
                        await send({"id": sequence, "kind": "ping"})
                        ping = await read()
                        if ping["epoch"] >= opened["epoch"] + 2:
                            break
                        sequence += 1
                        await asyncio.sleep(0.01)
                await resume()
                await frame(3, b"current-approved-frame")
                forwarded = []
                async with asyncio.timeout(3):
                    while True:
                        message = await read()
                        pixels = message.get("params", {}).get("data")
                        if pixels:
                            forwarded.append(base64.b64decode(pixels))
                        if pixels == base64.b64encode(b"current-approved-frame").decode():
                            break
                assert forwarded == [b"current-approved-frame"], forwarded
                await wait_for(lambda: {1, 2}.issubset(acknowledgements))
                # Overflow must also acknowledge evicted frames; otherwise
                # Chrome's small in-flight screencast window can stay full.
                pending_fence = None
                hold = True
                await frame(4, b"another-older-frame")
                await wait_for(lambda: pending_fence is not None)
                await frame(5, b"evicted-frame")
                for index in range(12):
                    await navigate(approved + f"?navigation={index}")
                await wait_for(lambda: 5 in acknowledgements)
                await resume()
                await wait_for(lambda: 4 in acknowledgements)
                # Several full-size frames can already be queued while the
                # first one is being checked. They must cause only one downgrade.
                pending_fence = None
                hold = True
                await frame(20, b"x" * 700000)
                await wait_for(lambda: pending_fence is not None)
                await frame(21, b"x" * 700000)
                await frame(22, b"x" * 700000)
                await asyncio.sleep(0.05)
                await resume()
                await wait_for(lambda: {20,21,22}.issubset(acknowledgements))
                assert capture_sizes == [1280], capture_sizes
                # A previously encoded full-HD frame may arrive only after the
                # new stream setting is active. Its dimensions still identify it.
                output = io.BytesIO()
                Image.effect_noise((1920,1080),100).convert("RGB").save(output,"JPEG",quality=95)
                oversized=output.getvalue()
                assert len(base64.b64encode(oversized))>800000
                await frame(23,oversized)
                await wait_for(lambda: 23 in acknowledgements)
                assert capture_sizes == [1280], capture_sizes
            finally:
                process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except TimeoutError:
                    process.kill()
                    await process.wait()
                assert process.returncode == 0, (await process.stderr.read()).decode()


asyncio.run(regression(Path(sys.argv[1])))
