from datetime import UTC, datetime, timedelta

from evander.repositories.execution_history import archive_execution_record
from evander.repositories.orchestration_db import (
    connect_orchestration_db,
    ensure_orchestration_schema,
)


def seed_indexed_history(path, *, executions, fields, age_days=40, node_id="history-node"):
    """Representative legacy and generation-indexed rows, without provider work."""
    ensure_orchestration_schema(path)
    at = datetime.now(UTC) - timedelta(days=age_days)
    template = {
        "execution_id": "0",
        "event_id": "event-0",
        "effect_id": node_id,
        "effect_type": "effect:event:emit",
        "status": "succeeded",
        "started_at": at,
        "finished_at": at,
        "input_payload": {},
        "outputs": {f"field-{number}": number for number in range(fields)},
        "directory_path": "root",
    }
    with connect_orchestration_db(path, deadline_seconds=60) as connection:
        archive_execution_record(connection, kind="effect", record=template)
        row = dict(connection.execute("SELECT * FROM workflow_node_execution_history").fetchone())
        columns = list(row)
        clones = []
        for number in range(1, executions):
            copy = {
                **row,
                "execution_key": f"effect:{number}",
                "execution_id": str(number),
                "event_id": f"event-{number}",
            }
            clones.append(tuple(copy[column] for column in columns))
        connection.executemany(
            "INSERT INTO workflow_node_execution_history ("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join("?" for _ in columns)
            + ")",
            clones,
        )
        connection.execute("DELETE FROM workflow_history_index_jobs")
        connection.execute("""INSERT INTO workflow_history_index_state
            SELECT execution_key,'seed' FROM workflow_node_execution_history
            WHERE CAST(execution_id AS INTEGER)%2=0""")
        connection.execute(
            """WITH RECURSIVE fields(n) AS (
            VALUES(0) UNION ALL SELECT n+1 FROM fields WHERE n+1<?)
            INSERT INTO workflow_history_index_values
            SELECT h.execution_key,'seed','output','/field-'||n,'number',NULL,CAST(n AS TEXT),n,NULL
            FROM workflow_node_execution_history h CROSS JOIN fields
            WHERE CAST(h.execution_id AS INTEGER)%2=0""",
            (fields,),
        )
        connection.execute(
            """WITH RECURSIVE fields(n) AS (
            VALUES(0) UNION ALL SELECT n+1 FROM fields WHERE n+1<?)
            INSERT INTO workflow_node_execution_history_values
            SELECT h.execution_key,'output','/field-'||n,'number',NULL,CAST(n AS TEXT),n,NULL
            FROM workflow_node_execution_history h CROSS JOIN fields
            WHERE CAST(h.execution_id AS INTEGER)%2=1""",
            (fields,),
        )
    return template
