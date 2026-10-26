"""Request-owned, immutable lazy reads from one committed Team SQLite snapshot."""

from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
import re
from typing import get_args, get_type_hints

from pydantic import TypeAdapter

from .models import TeamState
from .sqlite_persistence_map import _PERSISTENCE_GROUPS
from .sqlite_store import _STATE_GROUP_READERS


_TABLES = {
    field: tuple((table, key) for table, key, owner in specs if owner == field)
    for fields, specs, writer in _PERSISTENCE_GROUPS
    for field in fields
}
_MODELS = {
    field: TypeAdapter(get_args(TeamState.model_fields[field].annotation)[1]) for field in _TABLES
}
_SCOPE = ContextVar("team_mcp_read_scope", default=None)


class _SelectedConnection:
    def __init__(self, connection, selections):
        self.connection, self.selections = connection, selections

    def execute(self, sql, parameters=()):
        names = set(re.findall(r"\b(?:FROM|JOIN)\s+(team_\w+)", sql, re.IGNORECASE))
        ctes, values = [], []
        for table in sorted(names & self.selections.keys()):
            key, ids = self.selections[table]
            ctes.append(
                f"{table} AS (SELECT * FROM main.{table} WHERE {key} IN ({','.join('?' for _ in ids)}))"
            )
            values.extend(ids)
        if ctes:
            sql = "WITH " + ",".join(ctes) + " " + sql
        return self.connection.execute(sql, [*values, *parameters])


class _Rows(Mapping):
    def __init__(self, snapshot, field):
        self._snapshot, self._field = snapshot, field
        self._values, self._missing, self._all = {}, set(), False

    def _load(self, ids=None):
        if self._all:
            return
        if ids is not None:
            ids = [key for key in ids if key not in self._values and key not in self._missing]
            if not ids:
                return
        rows = self._snapshot.load(self._field, ids)
        self._values.update(
            {key: _MODELS[self._field].validate_python(value) for key, value in rows.items()}
        )
        if ids is None:
            self._all = True
        else:
            self._missing.update(set(ids) - rows.keys())

    def __getitem__(self, key):
        if not isinstance(key, str):
            raise KeyError(key)
        self._load([key])
        return self._values[key]

    def __iter__(self):
        self._load()
        return iter(self._values)

    def __len__(self):
        self._load()
        return len(self._values)

    def values(self):
        self._load()
        return self._values.values()

    def items(self):
        self._load()
        return self._values.items()


class TeamMcpReadSnapshot:
    """Not a writable TeamState. Keyed reads hydrate only that record's rows.

    Enumeration retains existing visibility/assessment behavior but touches only
    collections actually used by the tool. All reads share the same transaction;
    neither authorization nor derived values can mix different revisions.
    """

    # Pure relationship queries share the model's established semantics. No
    # validation, mutation, serialization or write API is exposed by this view.
    child_task_ids = TeamState.child_task_ids
    dependency_status = TeamState.dependency_status
    blocked_by_task_ids = TeamState.blocked_by_task_ids
    subtask_counts = TeamState.subtask_counts
    ordered_tasks_for_agent = TeamState.ordered_tasks_for_agent

    def __init__(self, store, connection):
        self._store, self._connection = store, connection
        metadata = dict(connection.execute("SELECT key,value FROM team_state_metadata"))
        for name in ("schema_version", "revision", "updated_at"):
            key = "state_schema_version" if name == "schema_version" else name
            value = metadata.get(
                key, TeamState.model_fields[name].get_default(call_default_factory=True)
            )
            setattr(
                self,
                name,
                TypeAdapter(TeamState.model_fields[name].annotation).validate_python(value),
            )
        self._rows = {name: _Rows(self, name) for name in _TABLES}

    def __getattr__(self, name):
        if name in self._rows:
            return self._rows[name]
        raise AttributeError(name)

    def has_operator_attention_candidates(self, agent_id):
        """Use existing indexes before the exceptional attention write path.

        This is only an empty-result check. Candidate messages still pass the
        existing current lease, visibility and delivery checks before exposure.
        """
        return (
            self._connection.execute(
                """SELECT 1 FROM team_conversations c
               JOIN team_conversation_messages m ON m.conversation_id=c.id
               WHERE c.agent_id=? AND m.sender='operator' AND m.message_type='text'
                 AND m.status IN ('queued','delivered_in_dispatch') LIMIT 1""",
                (agent_id,),
            ).fetchone()
            is not None
        )

    def load(self, field, ids):
        if self._connection is None:
            raise RuntimeError("Team read snapshot is closed")
        reader = _STATE_GROUP_READERS.get(field, "_read_" + field)
        selections = {}
        for other, tables in _TABLES.items():
            if other != field and _STATE_GROUP_READERS.get(other, "_read_" + other) == reader:
                selections.update({table: (key, []) for table, key in tables})
        if ids is not None:
            selections.update({table: (key, ids) for table, key in _TABLES[field]})
        payload = {name: {} for name in _TABLES}
        getattr(self._store, reader)(_SelectedConnection(self._connection, selections), payload)
        return payload[field]


class _ReadScope:
    def __init__(self, repository, stack):
        self.repository, self.stack, self.snapshot = repository, stack, None

    def read(self):
        if self.snapshot is None:
            self.snapshot = self.stack.enter_context(read_snapshot(self.repository.store))
        return self.snapshot


@contextmanager
def read_snapshot(store):
    with store._read_connection() as connection:
        connection.execute("BEGIN")
        snapshot = TeamMcpReadSnapshot(store, connection)
        try:
            yield snapshot
        finally:
            snapshot._connection = None


def current_read_snapshot(repository):
    scope = _SCOPE.get()
    return scope.read() if scope is not None and scope.repository is repository else None


class ScopedTeamToolRegistration:
    """Give each tool call an explicit lifetime, without changing its schema."""

    def __init__(self, server, repository):
        self._server, self._repository = server, repository

    def __getattr__(self, name):
        return getattr(self._server, name)

    def add_tool(self, function, *args, **kwargs):
        @wraps(function)
        def scoped(*call_args, **call_kwargs):
            with ExitStack() as stack:
                token = _SCOPE.set(_ReadScope(self._repository, stack))
                try:
                    return function(*call_args, **call_kwargs)
                finally:
                    _SCOPE.reset(token)

        scoped.__annotations__ = get_type_hints(function)
        scoped.__signature__ = inspect.signature(function, eval_str=True)
        return self._server.add_tool(scoped, *args, **kwargs)
