from __future__ import annotations

from evander_team_addon.sqlite_store import TeamStateSqliteStore


def test_multitable_team_state_reads_use_one_sqlite_snapshot(tmp_path, monkeypatch) -> None:
    store = TeamStateSqliteStore(tmp_path)
    original = store._read_state
    transaction_states: list[bool] = []

    def checked_read_state(connection):
        transaction_states.append(connection.in_transaction)
        return original(connection)

    monkeypatch.setattr(store, "_read_state", checked_read_state)

    store.read_state()
    store.read_object()

    assert transaction_states == [True, True]
