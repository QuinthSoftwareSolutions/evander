import os
from types import SimpleNamespace

import pytest

from evander.session_artifacts import read_artifact_text_no_follow


def test_bounded_artifact_counts_utf8_bytes_and_preserves_content(tmp_path):
    (tmp_path / "report.txt").write_bytes("é\r\n".encode())
    assert read_artifact_text_no_follow(tmp_path, "report.txt", max_bytes=4) == "é\r\n"
    with pytest.raises(ValueError, match="exceeds import limit"):
        read_artifact_text_no_follow(tmp_path, "report.txt", max_bytes=3)


def test_large_artifact_is_rejected_before_any_file_read(tmp_path, monkeypatch):
    path = tmp_path / "huge.txt"
    with path.open("wb") as handle:
        handle.truncate(1024 * 1024 * 1024)
    original = os.fdopen

    class NoRead:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def fileno(self):
            return self.handle.fileno()

        def read(self, *args):
            pytest.fail("oversized artifact must be rejected before reading")

    monkeypatch.setattr(os, "fdopen", lambda *args, **kwargs: NoRead(original(*args, **kwargs)))
    with pytest.raises(ValueError, match="exceeds import limit"):
        read_artifact_text_no_follow(tmp_path, "huge.txt", max_bytes=32)


def test_file_growth_after_stat_cannot_escape_read_limit(tmp_path, monkeypatch):
    (tmp_path / "growing.txt").write_bytes(b"x" * 100)
    original = os.fstat

    def stale_size(fd):
        result = original(fd)
        return SimpleNamespace(st_mode=result.st_mode, st_size=0)

    monkeypatch.setattr(os, "fstat", stale_size)
    with pytest.raises(ValueError, match="exceeds import limit"):
        read_artifact_text_no_follow(tmp_path, "growing.txt", max_bytes=32)


@pytest.mark.parametrize("max_bytes", [0, -1])
def test_artifact_byte_allowance_must_be_positive(tmp_path, max_bytes):
    with pytest.raises(ValueError, match="must be positive"):
        read_artifact_text_no_follow(tmp_path, "missing.txt", max_bytes=max_bytes)
