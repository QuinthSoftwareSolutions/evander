import json
import sqlite3
from evander_application_environments_addon.migration import check


def test_cutover_rejects_active_and_cleanup_failed_legacy_previews(tmp_path):
    root = tmp_path / "addons/browser-sessions-addon"
    root.mkdir(parents=True)
    with sqlite3.connect(root / "state.db") as db:
        db.executescript(
            "CREATE TABLE sessions(payload TEXT); CREATE TABLE settings(directory TEXT,revision INTEGER,payload TEXT);"
        )
        for id, mode, state in [
            ("active", "hosted_development", "ready"),
            ("cleanup", "hosted_demo", "cleanup_failed"),
            ("local", "user_hosted", "ready"),
            ("history", "hosted_demo", "closed"),
        ]:
            db.execute(
                "INSERT INTO sessions VALUES(?)", (json.dumps(dict(id=id, mode=mode, state=state)),)
            )
        db.execute(
            "INSERT INTO settings VALUES(?,?,?)",
            (
                "root",
                7,
                json.dumps(
                    {
                        "profiles": {
                            "web": {
                                "image": "app:1",
                                "browser_image": "browser:1",
                                "database_enabled": True,
                                "public_environment": {},
                            }
                        },
                        "workers": {},
                    }
                ),
            ),
        )
    blocked = check(tmp_path)
    assert (
        not blocked["ready"]
        and any("active" in item for item in blocked["blockers"])
        and any("cleanup" in item for item in blocked["blockers"])
    )
    assert not any("local" in item or "history" in item for item in blocked["blockers"])
    with sqlite3.connect(root / "state.db") as db:
        db.execute(
            "DELETE FROM sessions WHERE json_extract(payload,'$.id') IN ('active','cleanup')"
        )
    ready = check(tmp_path, sql_profiles={"web": "application-sql"})
    assert ready["ready"]
    proposal = ready["proposals"][0]
    assert proposal["expected_browser_revision"] == 7
    assert proposal["browser_settings"]["profiles"]["web"]["image"] == "browser:1"
    assert (
        proposal["application_settings"]["profiles"]["web"]["database_profile"] == "application-sql"
    )
    assert proposal["application_settings"]["allocations_enabled"] is False
    with sqlite3.connect(root / "state.db") as db:
        assert db.execute("SELECT revision FROM settings").fetchone()[0] == 7
