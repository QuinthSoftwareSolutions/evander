from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build_evander_godot_runner_images.sh"
IMAGE_NAMES = (
    "evander-runner-godot:",
    "evander-runner-godot-native:",
    "evander-runner-godot-render:",
)


def test_godot_image_scripts_have_valid_shell_syntax() -> None:
    scripts = [
        BUILD_SCRIPT,
        ROOT / "docker" / "evander-runner-godot" / "godot-launcher.sh",
        ROOT / "docker" / "evander-runner-godot-render" / "godot-render-launcher.sh",
    ]

    for script in scripts:
        subprocess.run(["bash", "-n", str(script)], cwd=ROOT, check=True)


def test_godot_variants_have_the_expected_capability_layers() -> None:
    godot = (ROOT / "docker" / "evander-runner-godot" / "Dockerfile").read_text(encoding="utf-8")
    native = (ROOT / "docker" / "evander-runner-godot-native" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    render = (ROOT / "docker" / "evander-runner-godot-render" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    render_launcher = (
        ROOT / "docker" / "evander-runner-godot-render" / "godot-render-launcher.sh"
    ).read_text(encoding="utf-8")
    launcher = (ROOT / "docker" / "evander-runner-godot" / "godot-launcher.sh").read_text(
        encoding="utf-8"
    )

    assert "ARG EVANDER_RUNNER_BASE_IMAGE=evander-runner:latest" in godot
    assert 'org.evander.runner.auto-deploy="false"' in godot
    assert "COPY export_templates/" in godot
    assert "LICENSE.godot.txt" in godot
    assert (ROOT / "docker" / "evander-runner-godot" / "LICENSE.godot.txt").is_file()
    assert "GODOT_EXPORT_TEMPLATES_DIR" in launcher
    assert "ln -s" in launcher

    assert "ARG EVANDER_GODOT_BASE_IMAGE=evander-runner-godot:4.7.1" in native
    for package in ("build-essential", "clang", "cmake", "ninja-build", "pkg-config", "scons"):
        assert package in native

    assert "ARG EVANDER_GODOT_BASE_IMAGE=evander-runner-godot:4.7.1" in render
    for package in ("libgl1-mesa-dri", "mesa-vulkan-drivers", "xauth", "xvfb"):
        assert package in render
    assert "godot-render-launcher.sh" in render
    assert "audio_args=(--audio-driver Dummy)" in render_launcher
    assert "display_args=(--display-driver x11)" in render_launcher
    assert "GALLIUM_DRIVER" in render_launcher


def test_default_worker_deployment_excludes_godot_images() -> None:
    worker_vars = (ROOT / "ansible" / "group_vars" / "evander_worker_hosts.yml").read_text(
        encoding="utf-8"
    )
    archive_block = worker_vars.split("evander_runner_image_archives:", 1)[1].split(
        "# Worker-level read-only caches", 1
    )[0]

    assert "scripts/build_evander_runner_image.sh" in archive_block
    assert "scripts/build_evander_claude_runner_image.sh" in archive_block
    assert "build_evander_godot_runner_images.sh" not in archive_block
    assert all(image_name not in archive_block for image_name in IMAGE_NAMES)
    assert "evander_worker_preload_images: []" in worker_vars
