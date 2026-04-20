from __future__ import annotations

import ast
from pathlib import Path


def _import_names(source_path: str) -> set[str]:
    tree = ast.parse(Path(source_path).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            imported.add(module)
    return imported


def test_orchestration_wiring_modules_do_not_import_admin_service_directly() -> None:
    forbidden_suffix = "admin_service"
    effect_imports = _import_names("src/agent_sandbox/orchestration/wiring/effect.py")
    trigger_imports = _import_names("src/agent_sandbox/orchestration/wiring/trigger.py")

    assert all(not name.endswith(forbidden_suffix) for name in effect_imports)
    assert all(not name.endswith(forbidden_suffix) for name in trigger_imports)
