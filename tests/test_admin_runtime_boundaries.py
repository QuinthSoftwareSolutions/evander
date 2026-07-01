from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ADMIN_SERVICE_SYMBOL_RE = re.compile(r"\bAdmin" + r"Service\b")
ADMIN_SERVICE_MODULE_RE = re.compile(r"evander\.admin\.service(?!s)")


def test_admin_service_module_is_removed() -> None:
    assert not (REPO_ROOT / "src" / "evander" / "admin" / "service.py").exists()


def test_admin_runtime_does_not_reintroduce_admin_service_symbol() -> None:
    offenders: dict[str, list[str]] = {}
    for root_name in ("src", "tests"):
        for path in (REPO_ROOT / root_name).rglob("*.py"):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            hits: list[str] = []
            if ADMIN_SERVICE_SYMBOL_RE.search(text):
                hits.append("Admin" + "Service")
            if ADMIN_SERVICE_MODULE_RE.search(text):
                hits.append("evander.admin." + "service")
            if hits:
                offenders[str(path.relative_to(REPO_ROOT))] = hits
    assert offenders == {}
