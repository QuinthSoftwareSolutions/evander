"""The public report subset shared by publication and retained-archive reads."""

import re


def public_report_path(relative):
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    if relative in {"result.json", "target.json"}:
        return True
    if not relative.startswith("reports/"):
        return False
    writeup = re.fullmatch(r"reports/.+/findings/([a-z0-9][a-z0-9._-]*)/\1\.md", relative)
    # Native slugs such as "diagnostics" are report names, not private directories.
    private_parts = parts[:-2] if writeup else parts
    if any(part in {"diagnostics", "codex-home", "sessions", "log"} for part in private_parts):
        return False
    return bool(
        writeup
        or parts[-1] in {"report.md", "scan-manifest.json", "findings.json", "coverage.json"}
        or relative.endswith("/exports/results.sarif")
        or re.fullmatch(r"reports/.+/checkpoints/[^/]+\.json", relative)
        or re.fullmatch(r"reports/.+/hardening/(?:[a-z0-9_-]+/)*[a-z0-9_.-]+\.md", relative)
    )
