import json
from pathlib import Path

from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.models import SessionManifest, ToolName
from agent_sandbox.ownership import DirectoryOwnershipQueries
from agent_sandbox.storage import JsonStore


def test_directory_ownership_queries_list_direct_entities_only_by_default(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    directory_store = DirectoryStore(runtime_dir / "directories.json")
    directory_service = DirectoryService(directory_store)
    sessions = JsonStore(runtime_dir / "sessions.json", SessionManifest)

    team = directory_service.create(parent="root", name="team")
    child = directory_service.create(parent=team.id, name="child")

    sessions.put(
        "session-team",
        SessionManifest(
            session_id="session-team",
            vm_id="vm-team",
            directory_path=team.path,
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at="2030-01-01T00:00:00Z",
            metadata={"issue_number": "101"},
        ),
    )
    sessions.put(
        "session-child",
        SessionManifest(
            session_id="session-child",
            vm_id="vm-child",
            directory_path=child.path,
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at="2030-01-01T00:00:00Z",
            metadata={"issue_number": "102"},
        ),
    )

    pr_map_path = runtime_dir / "pr-map.json"
    pr_map_path.write_text(
        json.dumps(
            {
                "entries": {
                    "github:org/repo:1": {
                        "provider": "github",
                        "repo": "org/repo",
                        "pr_id": "1",
                        "session_id": "session-team",
                        "directory_path": team.path,
                    },
                    "github:org/repo:2": {
                        "provider": "github",
                        "repo": "org/repo",
                        "pr_id": "2",
                        "session_id": "session-child",
                        "directory_path": child.path,
                    },
                }
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    processed_path = runtime_dir / "ado-processed-work-items.json"
    processed_path.write_text(
        json.dumps(
            {
                "101": {
                    "session_id": "session-team",
                    "directory_path": team.path,
                    "yaml_path": str(runtime_dir / "work-item-101.yaml"),
                    "script_exit_code": 0,
                },
                "102": {
                    "session_id": "session-child",
                    "directory_path": child.path,
                    "yaml_path": str(runtime_dir / "work-item-102.yaml"),
                    "script_exit_code": 0,
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    queries = DirectoryOwnershipQueries(
        directories=directory_service,
        sessions=sessions,
        pr_map_path=pr_map_path,
        ado_processed_store_path=processed_path,
    )

    snapshot = queries.list_entities(team.path)

    assert [item.path for item in snapshot.descendants] == ["root/team/child"]
    assert {(entity.kind, entity.entity_id) for entity in snapshot.entities} == {
        ("session", "session-team"),
        ("vm", "vm-team"),
        ("issue", "101"),
        ("pr_mapping", "github:org/repo:1"),
        ("ado_work_item", "101"),
    }


def test_directory_ownership_queries_can_include_descendants_and_fallback_to_session_path(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    directory_store = DirectoryStore(runtime_dir / "directories.json")
    directory_service = DirectoryService(directory_store)
    sessions = JsonStore(runtime_dir / "sessions.json", SessionManifest)

    team = directory_service.create(parent="root", name="team")
    child = directory_service.create(parent=team.id, name="child")

    sessions.put(
        "session-child",
        SessionManifest(
            session_id="session-child",
            vm_id=None,
            directory_path=child.path,
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at="2030-01-01T00:00:00Z",
        ),
    )

    pr_map_path = runtime_dir / "pr-map.json"
    pr_map_path.write_text(
        json.dumps(
            {
                "entries": {
                    "github:org/repo:2": {
                        "provider": "github",
                        "repo": "org/repo",
                        "pr_id": "2",
                        "session_id": "session-child",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    queries = DirectoryOwnershipQueries(
        directories=directory_service,
        sessions=sessions,
        pr_map_path=pr_map_path,
    )

    snapshot = queries.list_entities(team.path, include_descendants=True)

    assert ("session", "session-child") in {(entity.kind, entity.entity_id) for entity in snapshot.entities}
    assert ("pr_mapping", "github:org/repo:2") in {
        (entity.kind, entity.entity_id) for entity in snapshot.entities
    }
