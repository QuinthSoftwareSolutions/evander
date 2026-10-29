from __future__ import annotations

import importlib.util
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
from threading import Thread
import wave
from urllib.parse import urlsplit

import pytest

from evander_demo_video_addon import worker


class PublicTargetProxy(BaseHTTPRequestHandler):
    """Synthetic proxy that exposes only one public target to the browser."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        target = urlsplit(self.path)
        if not target.scheme:
            # A direct loopback request bypassed the proxy; the test must detect it.
            status, body = 200, b"direct loopback"
        elif target.hostname == "approved.example.test":
            status = 200
            body = (Path(__file__).parent / "fixtures/application.html").read_bytes()
        else:
            status, body = 403, b"target denied"
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.mark.parametrize(
    "startup_delay",
    ["none", "page", "recorder"],
    ids=["normal", "slow-page-creation", "slow-recorder-start"],
)
def test_managed_browser_recordings_and_action_recovery(tmp_path, startup_delay):
    """Catch regressions in actual managed-context recording and mutation replay protection."""
    project = Path(__file__).parents[1]
    module = os.environ.get(
        "DEMO_PLAYWRIGHT_MODULE", str(project / "node_modules/playwright/index.js")
    )
    node = shutil.which("node")
    if not node or not Path(module).is_file():
        pytest.skip(
            "Install this add-on's Node fixture dependency and provision Playwright browsers"
        )
    spec = importlib.util.spec_from_file_location(
        "demo_video_review_fixture", project / "scripts/build_review_samples.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    server = fixture.ThreadingHTTPServer(("127.0.0.1", 0), fixture.Fixture)
    server.origin = f"http://127.0.0.1:{server.server_address[1]}"
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        launch_helper = project.parents[1] / "src/evander/assets/playwright-launch.mjs"
        env = {
            **os.environ,
            "DEMO_REVIEW_OUTPUT": str(tmp_path),
            "DEMO_FIXTURE_ORIGIN": server.origin,
            "DEMO_PLAYWRIGHT_MODULE": module,
            "DEMO_CAPTURE_HELPER": str(project / "evander_demo_video_addon/capture.mjs"),
            "EVANDER_PLAYWRIGHT_LAUNCH_HELPER": str(launch_helper),
            "HTTP_TARGET_RELAY_BASE_URL": server.origin + "/relay",
            "EVANDER_SESSION_ID": "review-42",
            "EVANDER_MCP_SESSION_TOKEN": "synthetic-proof",
            "EVANDER_HTTP_TARGET_BROWSER_PROXY_URL": "",
        }
        if startup_delay != "none":
            env["DEMO_BASE_LAUNCH_HELPER"] = str(launch_helper)
            env["DEMO_PAGE_CREATION_DELAY_MS"] = "750" if startup_delay == "page" else "0"
            env["DEMO_RECORDING_START_DELAY_MS"] = "750" if startup_delay == "recorder" else "0"
            env["EVANDER_PLAYWRIGHT_LAUNCH_HELPER"] = str(
                project / "tests/fixtures/slow_page_launch.mjs"
            )
        result = subprocess.run(
            [node, str(project / "tests/fixtures/capture_scenarios.mjs")],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["recovery_checks"] == 2
        count = 0
        for name in ("mixed", "bugfix", "backend"):
            for receipt in json.loads((tmp_path / name / "recorded.json").read_text()).values():
                path = tmp_path / name / receipt["clip"]
                assert worker.digest(path) == receipt["sha256"]
                assert receipt["trim_start"] < receipt["trim_end"]
                media = worker.probe(path, worker.tools())
                video = next(
                    stream for stream in media["streams"] if stream["codec_type"] == "video"
                )
                # Allow only the recorder's one-frame duration rounding, not time spent
                # creating a page before its video exists.
                frame_period = 1 / float(Fraction(video["r_frame_rate"]))
                assert 0 <= receipt["trim_start"] < float(media["format"]["duration"])
                assert receipt["trim_end"] <= float(media["format"]["duration"]) + frame_period
                count += 1
        assert count == 6
        # The calculation result needs to be fully in the recorded viewport,
        # rather than merely present in an off-screen DOM element.
        details = json.loads((tmp_path / "mixed/observations.json").read_text())["details"]
        assert 0 <= details["y"] and details["y"] + details["height"] < 710
        # The clip must actually show both states of a fast control change, rather than
        # beginning after the action because the recording encoder had not received a frame.
        receipt = json.loads((tmp_path / "mixed/recorded.json").read_text())["exclude-breaks"]
        box = json.loads((tmp_path / "mixed/observations.json").read_text())["checkbox"]
        manifest = tmp_path / "manifest.json"
        worker.atomic_json(
            manifest,
            {
                "schema_version": 1,
                "request": {
                    "repo": "sample-app",
                    "pr_id": "42",
                    "feedback_id": "capture-regression",
                    "title": "Show the effect of planned breaks",
                    "target": "demo1",
                    "source_commit": "1" * 40,
                    "build_commit": "2" * 40,
                    "build_run_id": "123",
                    "build_number": "capture-test",
                },
                "scenes": [
                    {
                        "id": "exclude-breaks",
                        "kind": "browser",
                        "title": "Count planned time",
                        "narration": "The score now uses the time when production was planned.",
                        "evidence": [
                            {"kind": "observed", "reference": "Local calculation fixture"}
                        ],
                        "clip": "mixed/" + receipt["clip"],
                        "trim_start": receipt["trim_start"],
                        "trim_end": receipt["trim_end"],
                    }
                ],
            },
        )
        audio = tmp_path / "speech.wav"
        with wave.open(str(audio), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(24000)
            stream.writeframes(
                b"".join(
                    struct.pack("<h", int(5000 * math.sin(i * 2 * math.pi * 440 / 24000)))
                    for i in range(24000)
                )
            )
        worker.import_audio(manifest, "exclude-breaks", audio)
        rendered = worker.render(manifest)
        from PIL import Image

        counts = []
        for name, seconds in (
            ("before", 0.1),
            ("after", rendered["duration_seconds"] - 0.15),
        ):
            frame = tmp_path / (name + ".png")
            worker.run(
                [
                    worker.tools()["ffmpeg"],
                    "-y",
                    "-v",
                    "error",
                    "-ss",
                    str(seconds),
                    "-i",
                    str(tmp_path / "explanation.mp4"),
                    "-frames:v",
                    "1",
                    str(frame),
                ]
            )
            with Image.open(frame).convert("RGB") as image:
                crop = image.crop(
                    (
                        int(box["x"]),
                        int(box["y"]),
                        int(box["x"] + box["width"]),
                        int(box["y"] + box["height"]),
                    )
                )
                counts.append(sum(1 for rgb in crop.get_flattened_data() if max(rgb) < 160))
        assert counts[1] > box["width"] * box["height"] / 2 and counts[0] < counts[1] / 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_public_browser_recording_uses_proxy_and_never_falls_back_from_managed_login(tmp_path):
    project = Path(__file__).parents[1]
    module = os.environ.get(
        "DEMO_PLAYWRIGHT_MODULE", str(project / "node_modules/playwright/index.js")
    )
    node = shutil.which("node")
    if not node or not Path(module).is_file():
        pytest.skip(
            "Install this add-on's Node fixture dependency and provision Playwright browsers"
        )
    server = ThreadingHTTPServer(("127.0.0.1", 0), PublicTargetProxy)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {
            **os.environ,
            "DEMO_REVIEW_OUTPUT": str(tmp_path),
            "DEMO_PLAYWRIGHT_MODULE": module,
            "DEMO_CAPTURE_HELPER": str(project / "evander_demo_video_addon/capture.mjs"),
            "EVANDER_PLAYWRIGHT_LAUNCH_HELPER": str(
                project.parents[1] / "src/evander/assets/playwright-launch.mjs"
            ),
            "HTTP_TARGET_RELAY_BASE_URL": origin + "/relay",
            "EVANDER_HTTP_TARGET_BROWSER_PROXY_URL": origin,
            "EVANDER_SESSION_ID": "example-session",
            "EVANDER_MCP_SESSION_TOKEN": "synthetic-proof",
        }
        result = subprocess.run(
            [node, str(project / "tests/fixtures/public_capture_scenarios.mjs")],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["success"] is True
        receipt = json.loads((tmp_path / "clips/public.json").read_text())
        clip = tmp_path / receipt["clip"]
        assert receipt["sha256"] == worker.digest(clip)
        assert any(
            stream["codec_type"] == "video"
            for stream in worker.probe(clip, worker.tools())["streams"]
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
