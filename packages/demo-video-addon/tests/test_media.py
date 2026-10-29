from __future__ import annotations

import math
import struct
import tarfile
from io import BytesIO
import wave

import pytest

from evander_demo_video_addon import worker


def audio(path, seconds=0.6):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(
            b"".join(
                struct.pack("<h", int(5000 * math.sin(i * 2 * math.pi * 440 / 24000)))
                for i in range(int(seconds * 24000))
            )
        )
    return path


def request_manifest(tmp_path, scene=None, request=None):
    path = tmp_path / "manifest.json"
    value = {
        "schema_version": 1,
        "request": request
        or {
            "repo": "sample-app",
            "pr_id": "42",
            "feedback_id": "feedback-1",
            "title": "Make the result understandable",
            "target": "demo1",
            "source_commit": "1" * 40,
            "build_commit": "2" * 40,
            "build_run_id": "123",
            "build_number": "2026.10.27.1",
        },
        "scenes": [
            scene
            or {
                "id": "purpose",
                "kind": "card",
                "title": "Why this matters",
                "body": "The same work can be measured against the intended production time.",
                "narration": "This option makes planned production time explicit.",
                "evidence": [{"kind": "illustration", "reference": "Synthetic arithmetic example"}],
            }
        ],
    }
    worker.atomic_json(path, value)
    source = audio(tmp_path / "speech.wav")
    worker.import_audio(path, value["scenes"][0]["id"], source)
    return path


def teams_request(**overrides):
    return {
        "kind": "teams",
        "channel_target": "chat:example-chat",
        "message_id": "message-17",
        "title": "How the process works",
        "summary": "Explain the principal steps.",
        **overrides,
    }


def test_render_real_mp4_captions_transcript_and_resume(tmp_path, monkeypatch):
    manifest = request_manifest(tmp_path)
    value = worker.load_manifest(manifest)
    second = {
        **value["scenes"][0],
        "id": "result",
        "title": "What the result means",
        "narration": "Now the result uses the production time we intended.",
    }
    value["scenes"].append(second)
    worker.atomic_json(manifest, value)
    worker.import_audio(manifest, "result", tmp_path / "speech.wav")
    result = worker.render(manifest)
    assert result["width"] == 1280 and result["height"] == 720
    assert 4 <= result["duration_seconds"] < 5
    transcript = (tmp_path / "transcript.txt").read_text()
    assert "This option makes planned production time explicit." in transcript
    assert "2" * 40 in transcript
    assert "AI-generated narration" in transcript
    binaries = worker.tools()
    tracks = worker.probe(tmp_path / "explanation.mp4", binaries)["streams"]
    assert not any(track["codec_type"] == "subtitle" for track in tracks)
    extracted = (tmp_path / "captions.srt").read_text()
    assert "This option makes planned production time explicit." in extracted.replace("\n", " ")
    assert "00:00:00,000 --> 00:00:00,600" in (tmp_path / "captions.srt").read_text()
    frame = tmp_path / "clean-picture.png"
    worker.run(
        [
            binaries["ffmpeg"],
            "-y",
            "-v",
            "error",
            "-ss",
            "0.2",
            "-i",
            str(tmp_path / "explanation.mp4"),
            "-frames:v",
            "1",
            str(frame),
        ]
    )
    from PIL import Image

    with Image.open(frame).convert("RGB") as picture:
        # Optional captions are separate, never pixels covering the demonstration.
        colors = picture.crop((100, 580, 1180, 700)).get_flattened_data()
        assert all(
            max(abs(channel - expected) for channel, expected in zip(rgb, (20, 34, 48))) < 5
            for rgb in colors
        )
    original_run = worker.run

    def checked(command, **kwargs):
        assert "libx264" not in command, "Completed video must not be encoded again"
        return original_run(command, **kwargs)

    monkeypatch.setattr(worker, "run", checked)
    assert worker.render(manifest)["reused"]


def test_audio_longer_than_footage_holds_last_frame(tmp_path):
    binaries = worker.tools()
    clip = tmp_path / "browser.webm"
    worker.run(
        [
            binaries["ffmpeg"],
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0xf04b31:s=1280x720:r=24:d=0.5",
            "-c:v",
            "libvpx-vp9",
            "-threads",
            "2",
            str(clip),
        ]
    )
    scene = {
        "id": "visible",
        "kind": "browser",
        "title": "The result",
        "clip": "browser.webm",
        "narration": "The result remains visible while we explain what it means.",
        "evidence": [{"kind": "observed", "reference": "Local fixture browser result"}],
    }
    manifest = request_manifest(tmp_path, scene)
    worker.import_audio(manifest, "visible", audio(tmp_path / "long.wav", 2.2))
    result = worker.render(manifest)
    assert 2.4 <= result["duration_seconds"] < 2.7
    frame = tmp_path / "last.png"
    worker.run(
        [
            binaries["ffmpeg"],
            "-y",
            "-v",
            "error",
            "-ss",
            "2.1",
            "-i",
            str(tmp_path / "explanation.mp4"),
            "-frames:v",
            "1",
            str(frame),
        ]
    )
    from PIL import Image

    with Image.open(frame) as image:
        r, g, b = image.getpixel((100, 100))[:3]
    assert r > 200 and g < 110 and b < 110


def test_changed_script_cannot_reuse_old_speech(tmp_path):
    path = request_manifest(tmp_path)
    value = worker.load_manifest(path)
    value["scenes"][0]["narration"] = "A different explanation needs different audio."
    worker.atomic_json(path, value)
    with pytest.raises(worker.VideoError, match="current text"):
        worker.render(path)


def test_path_traversal_and_symlink_media_are_rejected(tmp_path):
    with pytest.raises(worker.VideoError, match="escapes"):
        worker.local_path(tmp_path, "../other.wav")
    (tmp_path / "link.wav").symlink_to("/etc/passwd")
    with pytest.raises(worker.VideoError, match="symbolic"):
        worker.local_path(tmp_path, "link.wav")


def test_over_five_minutes_is_rejected_before_encoding(tmp_path):
    path = request_manifest(tmp_path)
    value = worker.load_manifest(path)
    scene = value["scenes"][0]
    value["scenes"] = [{**scene, "id": str(i)} for i in range(3)]
    worker.atomic_json(path, value)
    source = audio(tmp_path / "long.wav", 101)
    for i in range(3):
        worker.import_audio(path, str(i), source)
    with pytest.raises(worker.VideoError, match="five minutes"):
        worker.render(path)
    assert not (tmp_path / "explanation.mp4").exists()


def test_oversized_final_and_truncated_audio_are_rejected(tmp_path):
    large = tmp_path / "large.mp4"
    with large.open("wb") as stream:
        stream.truncate(worker.MAX_BYTES + 1)
    with pytest.raises(worker.VideoError, match="50 MiB"):
        worker.verify_video(large, worker.tools())
    source = audio(tmp_path / "truncated.wav")
    source.write_bytes(source.read_bytes()[:-2])
    with pytest.raises(worker.VideoError, match="truncated"):
        worker.audio_duration(source)


def test_publication_receipts_bind_to_files_and_keep_same_idempotency_key(tmp_path):
    path = request_manifest(tmp_path)
    value = worker.load_manifest(path)
    value["request"]["summary"] = "Explain how planned production time affects the result."
    value["limitations"] = "The example uses synthetic production records."
    worker.atomic_json(path, value)
    worker.render(path)
    assert (
        "The example uses synthetic production records."
        in (tmp_path / "transcript.txt").read_text()
    )
    with pytest.raises(worker.VideoError, match="Stage"):
        worker.publication(path)
    worker.publication(path, kind="video", artifact_id="comm_" + "a" * 32)
    worker.publication(path, kind="transcript", artifact_id="comm_" + "b" * 32)
    first = worker.publication(path)
    assert first["artifact_ids"] == ["comm_" + "a" * 32, "comm_" + "b" * 32]
    assert "Covered: Explain how planned production time affects the result." in first["body_text"]
    assert worker.publication(path)["idempotency_key"] == first["idempotency_key"]
    worker.publication(path, kind="captions", artifact_id="comm_" + "c" * 32)
    assert worker.publication(path)["artifact_ids"] == ["comm_" + c * 32 for c in "abc"]
    original = worker.load_manifest(path)
    changed = worker.load_manifest(path)
    changed["scenes"][0]["narration"] = "A revised explanation cannot publish the old media."
    worker.atomic_json(path, changed)
    with pytest.raises(worker.VideoError, match="explanation changed"):
        worker.publication(path)
    worker.atomic_json(path, original)
    worker.publication(path, reply_id="reply-9")
    assert worker.publication(path) == {"published": True, "reply_id": "reply-9"}
    (tmp_path / "transcript.txt").write_text("tampered")
    with pytest.raises(worker.VideoError, match="transcript has changed"):
        worker.publication(path)


def test_teams_video_renders_and_publishes_without_pr_metadata(tmp_path, capsys):
    path = request_manifest(tmp_path, request=teams_request())
    result = worker.render(path)
    assert result["width"] == 1280 and result["height"] == 720
    transcript = (tmp_path / "transcript.txt").read_text()
    assert "How the process works" in transcript
    assert "AI-generated narration" in transcript
    assert "PR " not in transcript and "Build:" not in transcript
    assert "This option makes planned production time explicit." in transcript

    worker.publication(path, kind="video", artifact_id="comm_" + "a" * 32)
    worker.publication(path, kind="transcript", artifact_id="comm_" + "b" * 32)
    publication = worker.publication(path)
    assert publication["channel_target"] == "chat:example-chat"
    assert publication["idempotency_key"].startswith("teams-video:")
    assert publication["artifact_ids"] == ["comm_" + "a" * 32, "comm_" + "b" * 32]
    assert "Explain the principal steps." in publication["body_text"]
    assert "PR " not in publication["body_text"] and "Build:" not in publication["body_text"]
    assert worker.publication(path) == publication

    assert worker.main(["published", "--manifest", str(path), "--message-id", "sent-42"]) == 0
    assert '"reply_id": "sent-42"' in capsys.readouterr().out
    assert worker.publication(path) == {"published": True, "reply_id": "sent-42"}
    with pytest.raises(worker.VideoError, match="published request is immutable"):
        worker.import_audio(path, "purpose", tmp_path / "speech.wav")


def test_teams_publication_identity_is_scoped_to_the_chat_and_original_message(tmp_path):
    identities = [
        ("chat:one", "message-1"),
        ("chat:one", "message-2"),
        ("chat:two", "message-1"),
    ]
    keys = []
    for index, (chat, message_id) in enumerate(identities):
        directory = tmp_path / str(index)
        directory.mkdir()
        path = request_manifest(
            directory, request=teams_request(channel_target=chat, message_id=message_id)
        )
        worker.render(path)
        worker.publication(path, kind="video", artifact_id="comm_" + "a" * 32)
        worker.publication(path, kind="transcript", artifact_id="comm_" + "b" * 32)
        keys.append(worker.publication(path)["idempotency_key"])
    assert len(set(keys)) == len(identities)


@pytest.mark.parametrize(
    "case",
    [
        teams_request(channel_target="user:example"),
        teams_request(channel_target="chat:other\nchat:example"),
        teams_request(message_id=""),
        teams_request(message_id="message\nother"),
        teams_request(kind="unknown"),
    ],
)
def test_teams_video_requires_an_existing_chat_and_message_identity(tmp_path, case):
    with pytest.raises(worker.VideoError):
        request_manifest(tmp_path, request=case)


def test_preflight_failure_never_writes_readiness_receipt(tmp_path):
    receipt = tmp_path / "ready.txt"
    receipt.write_text("ready:old-request\n")
    assert (
        worker.main(
            [
                "preflight",
                "--audio",
                str(tmp_path / "missing.wav"),
                "--recording",
                str(tmp_path / "missing.webm"),
                "--receipt",
                str(receipt),
                "--request-key",
                "42:7:9:123",
            ]
        )
        == 1
    )
    assert not receipt.exists()


def test_silent_provider_audio_cannot_be_published_as_narration(tmp_path):
    path = request_manifest(tmp_path)
    silence = tmp_path / "silence.wav"
    with wave.open(str(silence), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(b"\0\0" * 24000)
    worker.import_audio(path, "purpose", silence)
    with pytest.raises(worker.VideoError, match="usable narration"):
        worker.render(path)


@pytest.mark.parametrize("name,kind", [("../outside", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)])
def test_offline_installer_rejects_traversal_and_links(tmp_path, name, kind):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as target:
        entry = tarfile.TarInfo(name)
        entry.type = kind
        entry.linkname = "/etc/passwd"
        content = b"bad"
        entry.size = len(content) if kind == tarfile.REGTYPE else 0
        target.addfile(entry, BytesIO(content) if entry.size else None)
    with pytest.raises(worker.VideoError):
        worker.install(archive, tmp_path / "installed")
    assert not (tmp_path / "outside").exists()
