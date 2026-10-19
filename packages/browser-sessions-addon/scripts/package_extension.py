"""Build a deterministic MV3 Chrome ZIP."""
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
root = Path(__file__).resolve().parents[1]
source = root / "dist" / "extension"
target = root / "evander_browser_sessions_addon" / "ui_static" / "chrome-extension.zip"
with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
    for name in ("background.js", "manifest.json", "panel.html", "panel.js", "styles.css"):
        info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, (source / name).read_bytes())
print(target)
