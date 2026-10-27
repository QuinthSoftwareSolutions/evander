"""Build an offline relocatable Linux media bundle from trusted host tools."""

from __future__ import annotations

import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile

from .worker import VideoError, digest, preflight


def prepare_toolchain(output: Path, playwright_cache: Path) -> dict:
    preflight()
    recording = sorted(p for p in playwright_cache.glob("ffmpeg-*") if p.is_dir())
    if not recording:
        raise VideoError("Provide the host-seeded Playwright cache including ffmpeg-* revisions")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp)
        for directory in ("bin", "libexec", "lib", "fonts", "playwright-cache", "licenses"):
            (bundle / directory).mkdir()
        copied_sources = set()
        loader_name = None
        for name in ("ffmpeg", "ffprobe"):
            executable = Path(shutil.which(name)).resolve()
            copied_sources.add(executable)
            shutil.copy2(executable, bundle / "libexec" / name)
            result = subprocess.run(
                ["ldd", str(executable)], capture_output=True, text=True, check=False
            )
            if result.returncode:
                raise VideoError(
                    "The media bundle builder requires trusted dynamically linked Linux tools"
                )
            for dependency in re.findall(r"(?:=>\s*)?(/[^\s]+)", result.stdout):
                library = Path(dependency).resolve()
                copied_sources.add(library)
                if not library.is_file():
                    raise VideoError("A media runtime library is unavailable")
                # Keep the SONAME used by the loader, rather than its resolved version filename.
                filename = Path(dependency).name
                target = bundle / "lib" / filename
                if target.exists() and digest(target) != digest(library):
                    raise VideoError("Media libraries have conflicting names")
                shutil.copy2(library, target)
                if "ld-linux" in filename:
                    loader_name = filename
            if not loader_name:
                raise VideoError("The media toolchain requires a Linux ELF loader")
            wrapper = bundle / "bin" / name
            wrapper.write_text(
                "#!/bin/sh\nset -eu\n"
                'media_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
                f'exec "$media_root/lib/{loader_name}" --library-path "$media_root/lib" "$media_root/libexec/{name}" "$@"\n',
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
        font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        shutil.copy2(font, bundle / "fonts" / font.name)
        license_root = Path("/usr/share/doc/fonts-dejavu-core/copyright")
        if license_root.is_file():
            shutil.copy2(license_root, bundle / "fonts" / "LICENSE.txt")
        if shutil.which("dpkg-query"):
            for source in sorted(copied_sources):
                owners = subprocess.run(
                    ["dpkg-query", "-S", str(source)], capture_output=True, text=True, check=False
                )
                for line in owners.stdout.splitlines():
                    owner = line.partition(": ")[0].split(":")[0]
                    notice = Path("/usr/share/doc") / owner / "copyright"
                    if notice.is_file():
                        shutil.copy2(notice, bundle / "licenses" / (owner + ".txt"))
        for directory in recording:
            shutil.copytree(directory, bundle / "playwright-cache" / directory.name, symlinks=False)
        package = Path(__file__).parent
        for name in ("worker.py", "capture.mjs"):
            shutil.copy2(package / name, bundle / name)
        preflight(bundle)
        inventory = {
            str(p.relative_to(bundle)): digest(p) for p in sorted(bundle.rglob("*")) if p.is_file()
        }
        (bundle / "bundle.json").write_text(
            json.dumps(
                {"schema_version": 1, "architecture": platform.machine(), "sha256": inventory},
                indent=2,
            )
            + "\n"
        )
        archive = output / "toolchain.tar.gz"
        temporary = output / "toolchain.tmp.tar.gz"
        with tarfile.open(temporary, "w:gz") as target:
            for item in sorted(bundle.rglob("*")):
                target.add(item, arcname=str(item.relative_to(bundle)), recursive=False)
        temporary.replace(archive)
        shutil.copy2(package / "worker.py", output / "worker.py")
        return {
            "archive": str(archive),
            "sha256": digest(archive),
            "bootstrap": str(output / "worker.py"),
            "recording_revisions": [p.name for p in recording],
        }
