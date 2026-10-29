"""Publish a standalone optional native helper; binaries are release artifacts, not Git files."""
import argparse
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--runtime", choices=("win-x64", "win-arm64", "linux-x64", "linux-arm64", "osx-x64", "osx-arm64"), required=True)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
target = args.output or root / "evander_browser_sessions_addon/ui_static" / ("helper-" + args.runtime + ".zip")
target.parent.mkdir(parents=True, exist_ok=True)
with TemporaryDirectory(prefix="evander-helper-build-") as folder:
    subprocess.run(["dotnet", "publish", str(root / "helper/Evander.BrowserHelper.csproj"), "-c", "Release", "-r", args.runtime,
                    "--self-contained", "true", "-p:PublishSingleFile=true", "-p:IncludeNativeLibrariesForSelfExtract=true",
                    "-p:EnableCompressionInSingleFile=true", "-p:DebugType=None", "-o", folder, "--nologo"], check=True, timeout=300)
    executable = "evander-browser-helper" + (".exe" if args.runtime.startswith("win-") else "")
    with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
        archive.write(Path(folder) / executable, executable)
        archive.write(root / "helper/README.txt", "README.txt")
        if args.runtime.startswith("win-"):
            archive.write(root / "helper/Install.cmd", "Install.cmd")
print(target)
