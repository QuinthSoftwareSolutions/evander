from __future__ import annotations

import signal

import evander.signal_cleanup as signal_cleanup


def test_signal_cleanup_handlers_chain_and_restore(monkeypatch) -> None:
    cleanup_calls: list[str] = []
    previous_calls: list[int] = []
    installed: dict[int, object] = {}

    def previous_handler(received: int, _frame: object) -> None:
        previous_calls.append(received)

    monkeypatch.setattr(signal_cleanup, "current_thread", lambda: "main")
    monkeypatch.setattr(signal_cleanup, "main_thread", lambda: "main")
    monkeypatch.setattr(signal_cleanup.signal, "getsignal", lambda _signum: previous_handler)
    monkeypatch.setattr(
        signal_cleanup.signal,
        "signal",
        lambda signum, handler: installed.__setitem__(signum, handler),
    )

    old_handlers = signal_cleanup.install_signal_cleanup_handlers(
        lambda: cleanup_calls.append("cleanup")
    )
    installed_handler = installed[signal.SIGTERM]
    assert callable(installed_handler)
    installed_handler(signal.SIGTERM, None)

    assert cleanup_calls == ["cleanup"]
    assert previous_calls == [signal.SIGTERM]
    assert old_handlers == {
        signal.SIGINT: previous_handler,
        signal.SIGTERM: previous_handler,
    }

    signal_cleanup.restore_signal_handlers(old_handlers)

    assert installed == {
        signal.SIGINT: previous_handler,
        signal.SIGTERM: previous_handler,
    }
