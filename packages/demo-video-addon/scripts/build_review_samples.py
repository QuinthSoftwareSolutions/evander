#!/usr/bin/env python3
"""Synthetic local evidence: actual seeded browser capture, host connector, local Flite speech.

No production requests or credentials. The substitute voice is deliberately labelled;
this does not establish the configured Speech API voice's quality or account access.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from threading import Thread
from types import SimpleNamespace
from urllib.request import urlopen

from evander.communication_artifacts import CommunicationArtifactStore
from evander.connectors.models import ConnectorDirectoryConfig
from evander.connectors.registry import ConnectorRegistry
from evander.connectors.service import ConnectorService
from evander.models import SessionManifest, ToolName
from evander_demo_video_addon.narration import NarrationConnectorDriver
from evander_demo_video_addon import worker


PROJECT = Path(__file__).resolve().parents[1]


def production_summary():
    """Synthetic backend change: summaries exclude the agreed planned break time."""
    shift, breaks, running, made, good = 480, 60, 336, 280, 252
    planned = shift - breaks
    availability = Decimal(running) / planned
    pace = Decimal(made) / running
    quality = Decimal(good) / made
    return {
        "planned_minutes": planned,
        "running_minutes": running,
        "pieces_made": made,
        "good_pieces": good,
        "effectiveness_percent": str(
            (availability * pace * quality * 100).quantize(Decimal("0.1"))
        ),
    }


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, content, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.path == "/report-summary":
            return self.send(200, json.dumps(production_summary()).encode(), "application/json")
        self.send(
            200,
            (PROJECT / "tests/fixtures/application.html").read_bytes(),
            "text/html; charset=utf-8",
        )

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", "0")))
        if "/website-session/demo-admin" in self.path:
            if self.headers.get("x-evander-mcp-session-token") != "synthetic-proof":
                return self.send(403, b"denied", "text/plain")
            value = {
                "expiresAt": (datetime.now(UTC) + timedelta(minutes=20)).timestamp(),
                "origin": self.server.origin,
                "storageState": {"cookies": [], "origins": []},
                "sessionStorage": {},
            }
            return self.send(200, json.dumps(value).encode(), "application/json")
        if (
            self.path != "/v1/audio/speech"
            or self.headers.get("authorization") != "Bearer synthetic-speech-key"
        ):
            return self.send(403, b"denied", "text/plain")
        data = json.loads(body)
        text = data["input"]
        if len(text) > 2000 or data["response_format"] != "wav":
            return self.send(400, b"invalid", "text/plain")
        with tempfile.TemporaryDirectory(prefix="demo-speech-") as tmp:
            root = Path(tmp)
            (root / "text.txt").write_text(text)
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "flite=textfile=text.txt:voice=slt",
                    "-ar",
                    "24000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    "speech.wav",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                timeout=30,
            )
            return self.send(200, (root / "speech.wav").read_bytes(), "audio/wav")


def scenes_for(name, recordings):
    def card(id, title, body, narration, kind="illustration", reference=None):
        return {
            "id": id,
            "kind": "card",
            "title": title,
            "body": body,
            "narration": narration,
            "evidence": [
                {
                    "kind": kind,
                    "reference": reference
                    or "Synthetic fixture scenario and independently checked arithmetic",
                }
            ],
        }

    def browser(id, title, narration):
        receipt = recordings[id]
        return {
            "id": id,
            "kind": "browser",
            "title": title,
            "narration": narration,
            **{
                key: receipt[key]
                for key in ("clip", "trim_start", "trim_end", "markers", "result_seconds")
            },
            "evidence": [
                {"kind": "observed", "reference": f"Local browser assertion and clips/{id}.json"}
            ],
        }

    if name == "mixed":
        return [
            card(
                "purpose",
                "A fair production picture",
                "A planned break is different from lost production time.",
                "This change helps us understand a production score fairly. It makes a planned break different from time when we expected production but the equipment was not running.",
            ),
            card(
                "meaning",
                "What the score tells us",
                "Time running. Production pace. Good pieces.",
                "The score brings together time spent running, the pace of production, and the share of pieces that meet the quality standard. These are three parts of the same picture.",
            ),
            card(
                "shift",
                "Our example shift",
                "480 minutes in the shift. 60 minutes of planned breaks.",
                "Imagine an eight hour shift with one hour of planned breaks. That leaves seven hours when we actually planned to produce. The choice of time matters to the final score.",
            ),
            browser(
                "starting-score",
                "The starting score",
                "With the full shift included, this example shows fifty two point five percent. The detail view tells us that all four hundred and eighty minutes are being counted.",
            ),
            card(
                "fair-time",
                "Count the time we intended",
                "420 planned production minutes after the break is excluded.",
                "If our agreed measure excludes planned breaks, we should compare production with four hundred and twenty minutes. We are changing the comparison, not pretending that more work was done.",
            ),
            browser(
                "exclude-breaks",
                "Choose how breaks are counted",
                "We select Exclude planned breaks. The score becomes sixty percent. The same production counts are now compared with the time when production was actually planned.",
            ),
            card(
                "counts",
                "Use a concrete example",
                "280 pieces made. 252 good pieces. One minute per piece at the agreed pace.",
                "In this example, two hundred and eighty pieces were made, and two hundred and fifty two were good. At the agreed pace, each good piece represents one minute of useful production.",
            ),
            card(
                "calculation",
                "Why the result is 60 percent",
                "252 good production minutes out of 420 planned minutes = 60%.",
                "That gives us two hundred and fifty two good production minutes out of four hundred and twenty planned minutes. Dividing those numbers gives sixty percent. This is the practical meaning of the calculation.",
            ),
            browser(
                "see-details",
                "Check the detail behind the score",
                "The detail view now shows four hundred and twenty planned minutes. This connects the setting in the form to the calculation behind the score, so we can explain where the result came from.",
            ),
            card(
                "unchanged",
                "The work itself is unchanged",
                "Use one agreed definition when comparing shifts.",
                "The work itself has not changed. Only the way planned breaks are counted has changed. When comparing shifts, use the same agreed definition, so a change in settings does not look like an improvement in production.",
            ),
            card(
                "use",
                "When to use the new control",
                "Choose the definition your production team has agreed to use.",
                "Use this control to make planned break time explicit. Choose the definition your team has agreed to use, rather than the option that simply gives a higher score.",
            ),
            card(
                "limits",
                "What this example establishes",
                "Synthetic example. Browser behavior checked. Arithmetic checked independently.",
                "You have seen the control and its result in a checked example. A real rollout should also check representative production data before drawing conclusions about performance.",
            ),
        ]
    if name == "bugfix":
        return [
            card(
                "purpose",
                "Trust the save message",
                "The message should describe the note you are looking at now.",
                "When we edit a production note, we need to know whether the latest words have been saved. This fix keeps that message in step with what is on the screen.",
            ),
            browser(
                "saved-note",
                "Save a note",
                "We write a note and select Save note. The Saved message confirms that this version of the note has been stored.",
            ),
            browser(
                "changed-note",
                "Edit the saved note",
                "Now we add another instruction. The message changes to Unsaved changes immediately. It no longer gives us the impression that these new words have already been stored.",
            ),
            card(
                "result",
                "A small message with a useful purpose",
                "An edit needs another save.",
                "The practical result is simple. After an edit, the screen tells us that another save is needed. This helps prevent a colleague from leaving the page believing the latest note is already safe.",
            ),
            card(
                "limits",
                "What was checked",
                "The save and edit states were checked in a local synthetic application.",
                "This video shows the corrected save and edit states in a synthetic application. It does not show an old version or claim to test every possible way of leaving the page.",
            ),
        ]
    return [
        card(
            "purpose",
            "Make summary figures comparable",
            "A backend calculation can change what a report means.",
            "This change is behind the scenes. It makes a summary figure use the same definition of planned production time as the figures people discuss during a shift.",
            kind="source",
            reference="scripts/build_review_samples.py::production_summary and backend/report-response.json",
        ),
        browser(
            "context",
            "Where people see the figures",
            "This existing overview gives us the context. No new control is needed for this change. The important part is what the summary number means.",
        ),
        card(
            "example",
            "Use the same example",
            "252 good production minutes. 420 planned production minutes.",
            "Take two hundred and fifty two good production minutes out of four hundred and twenty planned minutes. That is sixty percent. The summary should preserve that meaning when the report is prepared.",
            kind="source",
            reference="scripts/build_review_samples.py::production_summary; independently checked as 252 / 420 * 100",
        ),
        card(
            "purpose-two",
            "Why this matters",
            "A report should not quietly use a different definition.",
            "If a summary quietly counts planned breaks differently, two reports can appear to disagree even when they describe the same work. A consistent definition makes that conversation much easier.",
        ),
        card(
            "evidence",
            "Explanation and demonstration",
            "The browser gives context. The calculation is explained from source and checked examples.",
            "The browser gives context here. The calculation is explained from the source and checked examples, rather than being presented as something this screen proves. That distinction matters.",
        ),
        card(
            "next",
            "What to check next",
            "Compare representative report figures using the agreed definition.",
            "The next useful check is to compare representative report figures using the agreed definition. This is a synthetic explanation of the change, not evidence from a live production report.",
        ),
    ]


def build(output: Path, node: str, playwright_module: str):
    output.mkdir(parents=True, exist_ok=True)
    # Independent expected arithmetic for the UI assertions: a different expression from the app.
    assert Decimal(252) / Decimal(480) * 100 == Decimal("52.500")
    assert Decimal(252) / Decimal(420) * 100 == Decimal("60.0")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    server.origin = f"http://127.0.0.1:{server.server_address[1]}"
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        environment = {
            **os.environ,
            "DEMO_REVIEW_OUTPUT": str(output),
            "DEMO_FIXTURE_ORIGIN": server.origin,
            "DEMO_PLAYWRIGHT_MODULE": playwright_module,
            "DEMO_CAPTURE_HELPER": str(PROJECT / "evander_demo_video_addon/capture.mjs"),
            "EVANDER_PLAYWRIGHT_LAUNCH_HELPER": str(
                PROJECT.parents[1] / "src/evander/assets/playwright-launch.mjs"
            ),
            "HTTP_TARGET_RELAY_BASE_URL": server.origin + "/relay",
            "EVANDER_SESSION_ID": "review-42",
            "EVANDER_MCP_SESSION_TOKEN": "synthetic-proof",
            "EVANDER_HTTP_TARGET_BROWSER_PROXY_URL": "",
        }
        if all(
            (output / name / "recorded.json").is_file() for name in ("mixed", "bugfix", "backend")
        ):
            print(json.dumps({"recordings_reused": True}), flush=True)
        else:
            result = subprocess.run(
                [node, str(PROJECT / "tests/fixtures/capture_scenarios.mjs")],
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode:
                raise RuntimeError(result.stderr)
            print(result.stdout.strip(), flush=True)
        with urlopen(server.origin + "/report-summary", timeout=5) as response:
            report = json.load(response)
        expected = Decimal(252) / Decimal(420) * 100
        assert Decimal(report["effectiveness_percent"]) == expected
        assert report["planned_minutes"] == 420
        worker.atomic_json(output / "backend/report-response.json", report)
        config_data = json.loads((PROJECT / "examples/connectors.json").read_text())
        connector = config_data["connectors"]["demo-narration"]
        connector["disabled"] = False
        connector["config"]["base_url"] = server.origin + "/v1"
        connector["config"]["speech_backend"] = "speech_api"
        connector["config"]["model"] = "fixture-speech"
        config = ConnectorDirectoryConfig.model_validate(config_data)
        session = SessionManifest(
            session_id="review-42",
            directory_path="root/example",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            allowed_tools=[ToolName.CALL_CONNECTOR],
            connector_grants={"demo-narration": ["synthesize"]},
        )
        store = CommunicationArtifactStore(output / "host-runtime")
        registry = ConnectorRegistry()
        registry.register("demo_video_narration", NarrationConnectorDriver())
        service = ConnectorService(
            runtime_dir=output / "host-runtime",
            provider_config=SimpleNamespace(
                resolve_connectors_for_directory=lambda directory: config
            ),
            artifact_store_factory=lambda: store,
            registry=registry,
            environ={"DEMO_SPEECH_API_KEY": "synthetic-speech-key"},
            session_authorizer=lambda sid: session,
            session_argument_authorizer=lambda *args: None,
        )
        samples = []
        for index, name in enumerate(("mixed", "bugfix", "backend"), 42):
            job = output / name
            recordings = json.loads((job / "recorded.json").read_text())
            scenes = scenes_for(name, recordings)
            manifest_path = job / "manifest.json"
            titles = {
                "mixed": "A fair production picture",
                "bugfix": "Know when a saved note changes",
                "backend": "Make summary figures comparable",
            }
            manifest = {
                "schema_version": 1,
                "request": {
                    "repo": "sample-app",
                    "pr_id": str(index),
                    "feedback_id": f"synthetic-{name}",
                    "title": titles[name],
                    "target": "local-fixture",
                    "source_commit": "1" * 40,
                    "build_commit": "2" * 40,
                    "build_run_id": "123",
                    "build_number": "synthetic-review",
                },
                "limitations": "Synthetic local application and Flite speech substitute. No production or external speech-provider validation.",
                "scenes": scenes,
            }
            if manifest_path.is_file():
                previous = worker.load_manifest(manifest_path)
                if previous["request"] != manifest["request"]:
                    raise ValueError("The review output belongs to a different request")
                manifest["completed"] = previous.get("completed", {})
                old_scenes = {s["id"]: s for s in previous["scenes"]}
                for scene in scenes:
                    old = old_scenes.get(scene["id"], {})
                    if old.get("narration") == scene["narration"] and "audio" in old:
                        scene["audio"] = old["audio"]
            worker.atomic_json(manifest_path, manifest)
            for scene in scenes:
                known = manifest.get("completed", {}).get("narration", {}).get(scene["id"], {})
                if (
                    scene.get("audio")
                    and (job / scene["audio"]).is_file()
                    and known.get("text_sha256")
                    == hashlib.sha256(scene["narration"].encode()).hexdigest()
                    and known.get("sha256") == worker.digest(job / scene["audio"])
                ):
                    continue
                receipt = asyncio.run(
                    service.invoke(
                        session=session,
                        connector_id="demo-narration",
                        operation_name="synthesize",
                        arguments={"text": scene["narration"]},
                    )
                )
                artifact = store.get(receipt.artifact.artifact_id)
                worker.import_audio(manifest_path, scene["id"], artifact.blob_path)
            rendered = worker.render(manifest_path)
            binaries = worker.tools()
            for number, fraction in enumerate((0.1, 0.45, 0.8)):
                worker.run(
                    [
                        binaries["ffmpeg"],
                        "-y",
                        "-v",
                        "error",
                        "-ss",
                        str(rendered["duration_seconds"] * fraction),
                        "-i",
                        str(job / "explanation.mp4"),
                        "-frames:v",
                        "1",
                        str(job / f"review-frame-{number}.png"),
                    ]
                )
            samples.append({"name": name, **rendered})
            print(
                json.dumps(
                    {
                        "sample": name,
                        "seconds": rendered["duration_seconds"],
                        "bytes": rendered["byte_count"],
                    }
                ),
                flush=True,
            )
        worker.atomic_json(
            output / "review.json",
            {
                "speech_provider": "Local Flite substitute through the real narration connector",
                "production_requests": 0,
                "samples": samples,
            },
        )
        return samples
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--node", default=shutil.which("node"))
    parser.add_argument("--playwright-module", required=True)
    options = parser.parse_args()
    if not options.node:
        parser.error("Provide --node")
    build(options.output.resolve(), options.node, options.playwright_module)
