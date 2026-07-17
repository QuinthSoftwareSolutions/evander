from __future__ import annotations

import ast
from pathlib import Path


def test_events_models_contract_module_does_not_import_provider_packages() -> None:
    module_path = Path(__file__).resolve().parents[1] / "src" / "evander" / "events" / "models.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))

    forbidden_prefixes = (
        "evander.effects.providers",
        "evander.triggers.providers",
        "evander.queries.providers",
    )

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    offenders = sorted(
        imported
        for imported in imported_modules
        if any(imported.startswith(prefix) for prefix in forbidden_prefixes)
    )
    assert offenders == []
