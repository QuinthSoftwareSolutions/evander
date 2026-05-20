from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "operator-field-help-inventory.md"


def _table_rows(contents: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in contents.splitlines():
        if not line.startswith("|"):
            continue
        if line.startswith("|---"):
            continue
        if "Screen / component" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 7 and cells[0]:
            rows.append(cells)
    return rows


def test_operator_field_help_inventory_is_structured_and_discoverable() -> None:
    assert DOC.exists()
    contents = DOC.read_text(encoding="utf-8")

    assert "# Operator Field Help Inventory" in contents
    assert "## Inventory" in contents
    assert "| Screen / component | Field / control name | Current behavior | User confusion or risk | Recommended help treatment | Priority | Source evidence |" in contents

    for label in [
        "tooltip",
        "inline help",
        "validation copy",
        "structured editor candidate",
        "empty-state copy",
        "documentation link",
    ]:
        assert f"`{label}`" in contents

    for priority in ["operator-critical", "lower-priority"]:
        assert f"`{priority}`" in contents

    for source in [
        "frontend/workflow-editor/src/App.tsx",
        "frontend/workflow-editor/src/features/approvals/ApprovalsScreen.tsx",
        "frontend/workflow-editor/src/features/sessions/SessionsScreen.tsx",
        "frontend/workflow-editor/src/features/toolCalls/ToolCallsPage.tsx",
        "frontend/workflow-editor/src/features/configuration/ConfigurationScreen.tsx",
    ]:
        assert source in contents

    rows = _table_rows(contents)
    workflow_rows = [row for row in rows if row[0].startswith("Workflow editor >")]
    admin_rows = [row for row in rows if row[0].startswith("Admin Web >")]

    assert len(workflow_rows) >= 5
    assert len(admin_rows) >= 5
