from __future__ import annotations

import re
from pathlib import Path


_PATTERN = re.compile(
    r"(?:from\s+evander\.daemons\.pr_feedback_worker\s+import|"
    r"import\s+evander\.daemons\.pr_feedback_worker\b)"
)


def test_non_daemon_modules_do_not_import_pr_feedback_daemon() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src" / "evander"

    offending: list[str] = []
    for path in src_root.rglob("*.py"):
        if "/daemons/" in path.as_posix():
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _PATTERN.search(line):
                offending.append(f"{path}:{line_number}: {line.strip()}")

    assert offending == [], "Non-daemon PR feedback daemon imports found:\n" + "\n".join(offending)
