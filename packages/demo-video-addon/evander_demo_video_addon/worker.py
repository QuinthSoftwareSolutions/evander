#!/usr/bin/env python3
"""Portable, stdlib-only worker CLI; copied into the isolated session, never a host service."""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import wave
from fractions import Fraction


WIDTH, HEIGHT, FPS = 1280, 720, 24
MAX_SECONDS = 300
MAX_BYTES = 50 * 1024 * 1024
ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")
SHA = re.compile(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?\Z")
ARTIFACT = re.compile(r"comm_[0-9a-f]{32}\Z")


class VideoError(ValueError):
    pass


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, encoding="utf-8"
    ) as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(path)


def local_path(root: Path, value: str, *, exists: bool = True) -> Path:
    """Manifest paths are regular files in this request, never arbitrary host/guest paths."""
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise VideoError("Media paths must be relative request paths")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {"..", "."} for part in relative.parts):
        raise VideoError("Media path escapes the request workspace")
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise VideoError("Media paths cannot follow symbolic links")
    if not path.resolve().is_relative_to(root.resolve()):
        raise VideoError("Media path escapes the request workspace")
    if exists and not path.is_file():
        raise VideoError(f"Missing media file: {value}")
    return path


def nonempty(value, label: str, maximum: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\0" in value:
        raise VideoError(f"{label} requires 1 to {maximum} characters")
    return value


def validate_request(request: dict) -> None:
    for field in (
        "repo",
        "pr_id",
        "feedback_id",
        "target",
        "build_run_id",
        "build_number",
        "title",
    ):
        nonempty(request.get(field), f"request.{field}", 512)
    for field in ("source_commit", "build_commit"):
        if not isinstance(request.get(field), str) or not SHA.fullmatch(request[field]):
            raise VideoError(f"request.{field} must identify the full pinned commit")
    if "summary" in request:
        nonempty(request["summary"], "request.summary", 600)


def narrative_digest(manifest: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            {key: manifest.get(key) for key in ("request", "scenes", "limitations")},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def load_manifest(path: Path) -> dict:
    if path.is_symlink():
        raise VideoError("The manifest must be a regular request file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or not isinstance(value.get("request"), dict):
        raise VideoError("Expected a version 1 demo manifest")
    validate_request(value["request"])
    if "limitations" in value:
        nonempty(value["limitations"], "limitations", 2000)
    scenes = value.get("scenes")
    if not isinstance(scenes, list) or len(scenes) > 60:
        raise VideoError("A demo contains at most 60 narrated beats")
    seen = set()
    for scene in scenes:
        scene_id = scene.get("id")
        if not isinstance(scene_id, str) or not ID.fullmatch(scene_id) or scene_id in seen:
            raise VideoError("Every narrated beat needs a unique safe id")
        seen.add(scene_id)
        if scene.get("kind") not in {"browser", "card"}:
            raise VideoError("Scene kind must be browser or card")
        nonempty(scene.get("title"), "scene.title", 100)
        nonempty(scene.get("narration"), "scene.narration", 2000)
        if scene["kind"] == "card":
            nonempty(scene.get("body"), "scene.body", 1000)
        elif not isinstance(scene.get("clip"), str):
            raise VideoError("Browser scenes require a recorded clip")
        evidence = scene.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise VideoError("Each beat needs observed, source, or illustration evidence")
        for item in evidence:
            if item.get("kind") not in {"observed", "source", "illustration"}:
                raise VideoError("Unsupported evidence kind")
            nonempty(item.get("reference"), "evidence.reference", 2000)
        for field in ("trim_start", "trim_end", "hold_before"):
            v = scene.get(field)
            if v is not None and (
                type(v) not in {int, float} or not math.isfinite(v) or v < 0 or v > MAX_SECONDS
            ):
                raise VideoError(f"{field} must be a finite number between 0 and 300")
    return value


def run(
    command: list[str], *, cwd: Path | None = None, timeout: int = 180, include_stderr: bool = False
) -> str:
    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        raise VideoError(f"Media command exceeded its {timeout} second deadline") from None
    if result.returncode:
        raise VideoError("Media command failed: " + result.stderr[-1800:])
    return result.stdout + (result.stderr if include_stderr else "")


def tools(toolchain: Path | None = None) -> dict[str, str]:
    root = toolchain or Path(__file__).resolve().parent
    result = {}
    for name in ("ffmpeg", "ffprobe"):
        candidate = root / "bin" / name
        value = str(candidate) if candidate.is_file() else shutil.which(name)
        if not value:
            raise VideoError(f"Missing {name}; install the reviewed offline media toolchain")
        result[name] = value
    font = root / "fonts" / "DejaVuSans.ttf"
    if not font.is_file():
        font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if not font.is_file():
        raise VideoError("Missing the demo font; install the offline media toolchain")
    result["font"] = str(font)
    return result


def preflight(toolchain: Path | None = None) -> dict:
    binaries = tools(toolchain)
    encoders = run([binaries["ffmpeg"], "-hide_banner", "-encoders"], timeout=20)
    filters = run([binaries["ffmpeg"], "-hide_banner", "-filters"], timeout=20)
    for required in ("libx264", " aac "):
        if required not in encoders:
            raise VideoError(f"Media toolchain lacks encoder {required.strip()}")
    for required in ("drawtext", "tpad"):
        if required not in filters:
            raise VideoError(f"Media toolchain lacks filter {required}")
    run([binaries["ffprobe"], "-version"], timeout=20)
    return {
        "success": True,
        "binaries": binaries,
        "max_seconds": MAX_SECONDS,
        "max_bytes": MAX_BYTES,
    }


def probe(path: Path, binaries: dict) -> dict:
    return json.loads(
        run(
            [
                binaries["ffprobe"],
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            timeout=30,
        )
    )


def audio_duration(path: Path) -> float:
    if path.stat().st_size > 8_000_000:
        raise VideoError("Narration exceeds the 8 MB beat limit")
    try:
        with closing(wave.open(str(path), "rb")) as reader:
            frames, rate = reader.getnframes(), reader.getframerate()
            if (
                reader.getcomptype() != "NONE"
                or reader.getsampwidth() != 2
                or reader.getnchannels() not in {1, 2}
            ):
                raise VideoError("Narration must be uncompressed 16-bit WAV")
            expected = frames * reader.getnchannels() * reader.getsampwidth()
            if len(reader.readframes(frames)) != expected:
                raise VideoError("Narration WAV is truncated")
            duration = frames / rate
    except (wave.Error, EOFError):
        raise VideoError("Narration is not a valid WAV file") from None
    if not 0 < duration <= 120:
        raise VideoError("Narrated beats must last between 0 and 120 seconds")
    return duration


def caption_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def caption_text(text: str) -> str:
    # One short beat per optional cue, from exactly the spoken text.
    lines = textwrap.wrap(" ".join(text.split()), width=72)
    if len(lines) > 3:
        raise VideoError("Split long narration into shorter beats for readable captions")
    return "\n".join(lines)


def verify_video(path: Path, binaries: dict, *, decode: bool = True) -> dict:
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise VideoError("The final MP4 is missing or exceeds 50 MiB")
    info = probe(path, binaries)
    duration = float(info.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or not 0 < duration <= MAX_SECONDS:
        raise VideoError("The final MP4 must last at most five minutes")
    video = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    audio = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if (
        len(video) != 1
        or len(audio) != 1
        or video[0].get("codec_name") != "h264"
        or audio[0].get("codec_name") != "aac"
        or video[0].get("width") != WIDTH
        or video[0].get("height") != HEIGHT
    ):
        raise VideoError("The final MP4 requires 720p H.264 video and AAC audio")
    if (
        Fraction(video[0].get("avg_frame_rate", "0")) != FPS
        or video[0].get("pix_fmt") != "yuv420p"
        or audio[0].get("sample_rate") != "48000"
        or audio[0].get("channels") != 1
    ):
        raise VideoError("The final MP4 requires 24 fps, yuv420p and mono 48 kHz audio")
    if abs(float(audio[0].get("duration", 0)) - duration) > 0.25:
        raise VideoError("The final audio does not cover the video timeline")
    if decode:
        run(
            [
                binaries["ffmpeg"],
                "-v",
                "error",
                "-xerror",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(path),
                "-threads",
                "2",
                "-f",
                "null",
                "-",
            ],
            timeout=240,
        )
        levels = run(
            [
                binaries["ffmpeg"],
                "-v",
                "info",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(path),
                "-vn",
                "-af",
                "volumedetect",
                "-f",
                "null",
                "-",
            ],
            timeout=60,
            include_stderr=True,
        )
        peak = re.search(r"max_volume: (-?inf|[-0-9.]+) dB", levels)
        if peak is None or float(peak.group(1)) < -60:
            raise VideoError("The final MP4 contains no usable narration audio")
    return {
        "duration_seconds": duration,
        "byte_count": path.stat().st_size,
        "sha256": digest(path),
        "width": WIDTH,
        "height": HEIGHT,
    }


def render(manifest_path: Path, toolchain: Path | None = None) -> dict:
    manifest = load_manifest(manifest_path)
    if not manifest["scenes"]:
        raise VideoError("Prepare narrated scenes before rendering")
    root = manifest_path.resolve().parent
    binaries = preflight(toolchain)["binaries"]
    cache = local_path(root, ".render", exists=False)
    cache.mkdir(exist_ok=True)
    font = cache / "font.ttf"
    shutil.copyfile(binaries["font"], font)
    scenes = []
    total = 0.0
    for scene in manifest["scenes"]:
        caption_text(scene["narration"])
        audio = local_path(root, scene.get("audio", ""))
        narration_seconds = audio_duration(audio)
        audio_receipt = manifest.get("completed", {}).get("narration", {}).get(scene["id"], {})
        if audio_receipt.get("text_sha256") != hashlib.sha256(
            scene["narration"].encode()
        ).hexdigest() or audio_receipt.get("sha256") != digest(audio):
            raise VideoError(
                "Narration changed or has no matching receipt; import speech for the current text"
            )
        item = {
            "scene": scene,
            "audio": str(audio),
            "audio_sha256": digest(audio),
            "audio_seconds": narration_seconds,
            "renderer_sha256": digest(Path(__file__)),
        }
        visual_seconds = 2.0
        if scene["kind"] == "browser":
            clip = local_path(root, scene["clip"])
            info = probe(clip, binaries)
            duration = float(info["format"]["duration"])
            start = float(scene.get("trim_start", 0))
            end = float(scene.get("trim_end", duration))
            if not math.isfinite(duration) or not 0 <= start < end <= duration + 0.05:
                raise VideoError("Browser scene trim lies outside the recorded clip")
            visual_seconds = end - start + float(scene.get("hold_before", 0))
            item.update(clip=str(clip), clip_sha256=digest(clip), start=start, end=end)
        duration = math.ceil((max(narration_seconds, visual_seconds) + 0.25) * FPS) / FPS
        total += duration
        item["duration"] = duration
        item["fingerprint"] = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
        scenes.append(item)
    if total > MAX_SECONDS:
        raise VideoError(
            "Narration and footage exceed five minutes; shorten or split the explanation"
        )
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "request": manifest["request"],
                "limitations": manifest.get("limitations"),
                "scenes": scenes,
                "format": [WIDTH, HEIGHT, FPS, digest(Path(__file__))],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    completed = manifest.setdefault("completed", {})
    if (
        completed.get("publication", {}).get("reply_id")
        and completed.get("render", {}).get("fingerprint") != fingerprint
    ):
        raise VideoError("A published request is immutable; use a new PR command for another video")
    final = local_path(root, "explanation.mp4", exists=False)
    saved = completed.get("render", {})
    if (
        saved.get("fingerprint") == fingerprint
        and final.is_file()
        and saved.get("sha256") == digest(final)
        and local_path(root, "transcript.txt", exists=False).is_file()
        and saved.get("transcript_sha256") == digest(local_path(root, "transcript.txt"))
        and local_path(root, "captions.srt", exists=False).is_file()
        and saved.get("captions_sha256") == digest(local_path(root, "captions.srt"))
    ):
        verify_video(final, binaries)
        return {**saved, "reused": True}
    timing = []
    for item in scenes:
        scene = item["scene"]
        name = item["fingerprint"]
        segment = cache / (name + ".mp4")
        receipt = cache / (name + ".json")
        if not (
            segment.is_file()
            and receipt.is_file()
            and json.loads(receipt.read_text()).get("sha256") == digest(segment)
        ):
            work = cache / name
            work.mkdir(exist_ok=True)
            shutil.copyfile(font, work / "font.ttf")
            command = [
                binaries["ffmpeg"],
                "-y",
                "-v",
                "error",
                "-threads",
                "2",
                "-filter_threads",
                "1",
                "-protocol_whitelist",
                "file,pipe",
            ]
            if scene["kind"] == "card":
                if (
                    len(textwrap.wrap(scene["title"], 35)) > 2
                    or len(textwrap.wrap(scene["body"], 48)) > 7
                ):
                    raise VideoError("Split dense explanatory cards into shorter readable beats")
                command += [
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=0x142230:s={WIDTH}x{HEIGHT}:r={FPS}:d={item['duration']}",
                ]
                lines = []
                for field, width, size, top, spacing, color in (
                    ("title", 35, 46, 85, 58, "white"),
                    ("body", 48, 34, 230, 46, "0xcadce4"),
                ):
                    for index, line in enumerate(textwrap.wrap(scene[field], width)):
                        filename = f"{field}-{index}.txt"
                        (work / filename).write_text(line, encoding="utf-8")
                        lines.append(
                            f"drawtext=fontfile=font.ttf:textfile={filename}:expansion=none:fontcolor={color}:fontsize={size}:x=70:y={top + index * spacing}"
                        )
                filters = ",".join(lines)
            else:
                command += [
                    "-ss",
                    f"{item['start']:.6f}",
                    "-t",
                    f"{item['end'] - item['start']:.6f}",
                    "-i",
                    item["clip"],
                ]
                filters = f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=0x142230,fps={FPS},tpad=start_mode=clone:start_duration={scene.get('hold_before', 0)}:stop_mode=clone:stop_duration={item['duration']}"
            filters += ",drawtext=fontfile=font.ttf:text='AI narration':expansion=none:fontcolor=white:fontsize=16:x=w-tw-24:y=18"
            command += [
                "-i",
                item["audio"],
                "-vf",
                filters,
                "-af",
                f"apad=pad_dur={item['duration']}",
                "-t",
                str(item["duration"]),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-b:v",
                "1000k",
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(FPS),
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "48000",
                "-ac",
                "1",
                "-movflags",
                "+faststart",
                "segment.tmp.mp4",
            ]
            run(command, cwd=work)
            (work / "segment.tmp.mp4").replace(segment)
            atomic_json(receipt, {"sha256": digest(segment)})
        start = sum(t["duration_seconds"] for t in timing)
        segment_seconds = float(probe(segment, binaries)["format"]["duration"])
        timing.append(
            {
                "id": scene["id"],
                "start_seconds": start,
                "duration_seconds": segment_seconds,
                "audio_seconds": item["audio_seconds"],
                "file": segment.name,
            }
        )
    (cache / "segments.txt").write_text(
        "".join(f"file '{t['file']}'\n" for t in timing), encoding="utf-8"
    )
    (cache / "captions.srt").write_text(
        "\n".join(
            f"{index}\n{caption_time(beat['start_seconds'])} --> "
            f"{caption_time(beat['start_seconds'] + beat['audio_seconds'])}\n"
            f"{caption_text(scene['narration'])}\n"
            for index, (scene, beat) in enumerate(zip(manifest["scenes"], timing), 1)
        ),
        encoding="utf-8",
    )
    run(
        [
            binaries["ffmpeg"],
            "-y",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-f",
            "concat",
            "-safe",
            "1",
            "-i",
            "segments.txt",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-threads",
            "2",
            "-filter_threads",
            "1",
            "-vf",
            f"fps={FPS}",
            "-af",
            "aresample=async=1:first_pts=0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-b:v",
            "1000k",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(FPS),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-movflags",
            "+faststart",
            "explanation.tmp.mp4",
        ],
        cwd=cache,
    )
    result = verify_video(cache / "explanation.tmp.mp4", binaries)
    (cache / "explanation.tmp.mp4").replace(final)
    shutil.copyfile(cache / "captions.srt", local_path(root, "captions.srt", exists=False))
    request = manifest["request"]
    transcript = f"{request['title']}\nPR {request['pr_id']} | {request['repo']}\nTarget: {request['target']} | Build: {request['build_number']} ({request['build_run_id']})\nSource: {request['source_commit']}\nBuild revision: {request['build_commit']}\nAI-generated narration.\n\n"
    transcript += "\n\n".join(scene["narration"] for scene in manifest["scenes"])
    transcript += "\n\nEvidence and limitations\n" + "\n".join(
        f"- {scene['title']} [{e['kind']}]: {e['reference']}"
        for scene in manifest["scenes"]
        for e in scene["evidence"]
    )
    transcript += "\n\nLimitations: " + manifest.get(
        "limitations", "No additional limitations recorded."
    )
    local_path(root, "transcript.txt", exists=False).write_text(transcript + "\n", encoding="utf-8")
    result.update(
        fingerprint=fingerprint,
        video="explanation.mp4",
        transcript="transcript.txt",
        transcript_sha256=digest(local_path(root, "transcript.txt")),
        captions="captions.srt",
        captions_sha256=digest(local_path(root, "captions.srt")),
        manifest_sha256=narrative_digest(manifest),
        timeline=timing,
    )
    completed["render"] = result
    completed.pop("publication", None)
    atomic_json(manifest_path, manifest)
    return result


def import_audio(manifest_path: Path, scene_id: str, source: Path) -> dict:
    manifest = load_manifest(manifest_path)
    if manifest.get("completed", {}).get("publication", {}).get("reply_id"):
        raise VideoError("A published request is immutable; use a new PR command")
    scene = next((s for s in manifest["scenes"] if s["id"] == scene_id), None)
    if scene is None:
        raise VideoError("Unknown scene")
    if source.is_symlink() or not source.is_file() or source.stat().st_size > 8_000_000:
        raise VideoError("Import a regular WAV file of at most 8 MB")
    seconds = audio_duration(source)
    path = local_path(manifest_path.resolve().parent, f"audio/{scene_id}.wav", exists=False)
    path.parent.mkdir(exist_ok=True)
    shutil.copyfile(source, path)
    scene["audio"] = str(path.relative_to(manifest_path.resolve().parent))
    manifest.setdefault("completed", {}).setdefault("narration", {})[scene_id] = {
        "sha256": digest(path),
        "text_sha256": hashlib.sha256(scene["narration"].encode()).hexdigest(),
    }
    atomic_json(manifest_path, manifest)
    return {"path": scene["audio"], "duration_seconds": seconds}


def publication(
    manifest_path: Path,
    *,
    kind: str | None = None,
    artifact_id: str | None = None,
    reply_id: str | None = None,
) -> dict:
    manifest = load_manifest(manifest_path)
    root = manifest_path.resolve().parent
    completed = manifest.setdefault("completed", {})
    receipt = completed.get("render")
    if receipt and receipt.get("manifest_sha256") != narrative_digest(manifest):
        raise VideoError("The explanation changed; render again before publication")
    if not receipt or digest(local_path(root, receipt["video"])) != receipt["sha256"]:
        raise VideoError("Render and verify the complete video before publication")
    if digest(local_path(root, receipt["transcript"])) != receipt.get("transcript_sha256"):
        raise VideoError("The verified transcript has changed; render again before publication")
    state = completed.setdefault("publication", {})
    if state.get("reply_id"):
        return {"published": True, "reply_id": state["reply_id"]}
    if kind is not None:
        if (
            kind not in {"video", "transcript", "captions"}
            or not artifact_id
            or not ARTIFACT.fullmatch(artifact_id)
        ):
            raise VideoError(
                "Record a video/transcript/captions receipt with its staged artifact id"
            )
        state[kind] = {
            "artifact_id": artifact_id,
            "sha256": digest(local_path(root, receipt[kind])),
        }
        atomic_json(manifest_path, manifest)
        return {"staged": kind}
    for name in ("video", "transcript"):
        if state.get(name, {}).get("sha256") != digest(local_path(root, receipt[name])):
            raise VideoError(f"Stage the current {name} and record its receipt before posting")
    if "captions" in state and state["captions"].get("sha256") != digest(
        local_path(root, receipt["captions"])
    ):
        raise VideoError("Stage the current optional captions before posting")
    if reply_id:
        state["reply_id"] = nonempty(reply_id, "reply_id", 256)
        atomic_json(manifest_path, manifest)
        return {"published": True, "reply_id": reply_id}
    request = manifest["request"]
    demonstrated = (
        request.get("summary")
        or "; ".join(
            scene["title"]
            for scene in manifest["scenes"]
            if any(item["kind"] == "observed" for item in scene["evidence"])
        )[:600]
        or request["title"]
    )
    key = hashlib.sha256(
        json.dumps(
            {k: request[k] for k in ("repo", "pr_id", "feedback_id")}, sort_keys=True
        ).encode()
    ).hexdigest()
    return {
        "published": False,
        "idempotency_key": "pr-explain:" + key + ":final",
        "artifact_ids": [
            state[n]["artifact_id"] for n in ("video", "transcript", "captions") if n in state
        ],
        "body_text": f"Here is the explanation of {request['title']}.\n\nTarget: `{request['target']}`. Build: {request['build_number']} ({request['build_run_id']}).\nSource revision: `{request['source_commit']}`. Build revision: `{request['build_commit']}`.\n\nCovered: {demonstrated}\n\nThe video includes AI-generated narration. The transcript distinguishes what was demonstrated, explained from source, and illustrated.\n\nLimitations: "
        + manifest.get("limitations", "No additional limitations recorded."),
    }


def install(archive: Path, root: Path) -> dict:
    """Extract a reviewed host-seeded bundle without links, devices or path traversal."""
    if archive.stat().st_size > 1024 * 1024 * 1024:
        raise VideoError("Toolchain archive exceeds 1 GiB")
    root.mkdir(parents=True, exist_ok=True)
    version = digest(archive)
    destination = root / "toolchains" / version
    if not destination.exists():
        destination.parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
            staging = Path(tmp) / "bundle"
            staging.mkdir()
            with tarfile.open(archive, "r:gz") as bundle:
                members = bundle.getmembers()
                if sum(m.size for m in members) > 2 * 1024 * 1024 * 1024 or len(members) > 4096:
                    raise VideoError("Toolchain archive exceeds its extraction budget")
                names = set()
                for member in members:
                    if member.name in names or not (member.isfile() or member.isdir()):
                        raise VideoError(
                            "Toolchain archive contains a duplicate or unsupported entry"
                        )
                    names.add(member.name)
                    local_path(staging, member.name, exists=False)
                bundle.extractall(staging, filter="data")
            inventory = json.loads((staging / "bundle.json").read_text())
            if (
                inventory.get("schema_version") != 1
                or inventory.get("architecture") != platform.machine()
            ):
                raise VideoError(
                    "Toolchain bundle format or architecture does not match this worker"
                )
            files = {str(p.relative_to(staging)) for p in staging.rglob("*") if p.is_file()} - {
                "bundle.json"
            }
            if files != set(inventory["sha256"]):
                raise VideoError("Toolchain inventory does not match the archive")
            for name, expected in inventory["sha256"].items():
                if digest(local_path(staging, name)) != expected:
                    raise VideoError("Toolchain file failed its digest check")
            preflight(staging)
            staging.rename(destination)
    inventory = json.loads((destination / "bundle.json").read_text())
    for name, expected in inventory["sha256"].items():
        if digest(local_path(destination, name)) != expected:
            raise VideoError("Installed toolchain file failed its digest check")
    preflight(destination)
    current = root / "current"
    pending = root / ".current.tmp"
    pending.unlink(missing_ok=True)
    pending.symlink_to(destination.relative_to(root), target_is_directory=True)
    pending.replace(current)
    return {
        "success": True,
        "toolchain": str(current),
        "playwright_browsers_path": str(current / "playwright-cache"),
        "sha256": version,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "preflight",
            "render",
            "verify",
            "import-audio",
            "stage-receipt",
            "publication",
            "published",
            "install",
        ],
    )
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--toolchain", type=Path)
    parser.add_argument("--scene")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--kind", choices=["video", "transcript", "captions"])
    parser.add_argument("--artifact-id")
    parser.add_argument("--reply-id")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--recording", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--request-key")
    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            if args.receipt:
                args.receipt.unlink(missing_ok=True)
            result = preflight(args.toolchain)
            if args.receipt:
                if (
                    not args.audio
                    or not args.recording
                    or not args.request_key
                    or "\n" in args.request_key
                ):
                    parser.error(
                        "preflight receipt requires --audio, --recording and --request-key"
                    )
                audio_duration(args.audio)
                if not any(
                    s.get("codec_type") == "video"
                    for s in probe(args.recording, result["binaries"])["streams"]
                ):
                    raise VideoError("Recording preflight has no video")
                args.receipt.parent.mkdir(parents=True, exist_ok=True)
                args.receipt.write_text("ready:" + args.request_key + "\n", encoding="utf-8")
        elif args.command == "install":
            if not args.archive or not args.root:
                parser.error("install requires --archive and --root")
            result = install(args.archive, args.root)
        else:
            if not args.manifest:
                parser.error("this command requires --manifest")
            if args.command == "render":
                result = render(args.manifest, args.toolchain)
            elif args.command == "verify":
                result = verify_video(
                    args.manifest.resolve().parent / "explanation.mp4", tools(args.toolchain)
                )
            elif args.command == "import-audio":
                if not args.source or not args.scene:
                    parser.error("import-audio requires --scene and --source")
                result = import_audio(args.manifest, args.scene, args.source)
            elif args.command == "stage-receipt":
                if not args.kind or not args.artifact_id:
                    parser.error("stage-receipt requires --kind and --artifact-id")
                result = publication(args.manifest, kind=args.kind, artifact_id=args.artifact_id)
            elif args.command == "published":
                if not args.reply_id:
                    parser.error("published requires --reply-id from the successful reply tool")
                result = publication(args.manifest, reply_id=args.reply_id)
            else:
                result = publication(args.manifest)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (VideoError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
