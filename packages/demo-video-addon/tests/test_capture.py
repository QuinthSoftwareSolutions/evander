from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
from threading import Thread

import pytest

from evander_demo_video_addon import worker


def test_managed_browser_recordings_and_action_recovery(tmp_path):
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
        env = {
            **os.environ,
            "DEMO_REVIEW_OUTPUT": str(tmp_path),
            "DEMO_FIXTURE_ORIGIN": server.origin,
            "DEMO_PLAYWRIGHT_MODULE": module,
            "DEMO_CAPTURE_HELPER": str(project / "evander_demo_video_addon/capture.mjs"),
            "EVANDER_PLAYWRIGHT_LAUNCH_HELPER": str(
                project.parents[1] / "src/evander/assets/playwright-launch.mjs"
            ),
            "HTTP_TARGET_RELAY_BASE_URL": server.origin + "/relay",
            "EVANDER_SESSION_ID": "review-42",
            "EVANDER_MCP_SESSION_TOKEN": "synthetic-proof",
            "EVANDER_HTTP_TARGET_BROWSER_PROXY_URL": "",
        }
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
                assert any(
                    stream["codec_type"] == "video"
                    for stream in worker.probe(path, worker.tools())["streams"]
                )
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
        from PIL import Image

        counts = []
        for name, seconds in (
            ("before", receipt["trim_start"] + 0.1),
            ("after", receipt["trim_end"] - 0.15),
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
                    str(tmp_path / "mixed" / receipt["clip"]),
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
