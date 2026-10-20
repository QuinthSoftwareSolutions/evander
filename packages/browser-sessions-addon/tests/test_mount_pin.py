import os
from types import SimpleNamespace

import pytest

from evander_browser_sessions_addon.mount_pin import open_checked_workspace, pin_workspace


def test_pinning_uses_the_checked_inode_when_the_path_is_replaced(tmp_path, monkeypatch):
    root = tmp_path / "worker"
    source = root / "checkout"
    source.mkdir(parents=True)
    original = source.stat()
    target = tmp_path / "private" / "workspace"
    target.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    observed = []

    def mount(command, **kwargs):
        if "--bind" in command:
            source.rename(root / "retained")
            source.symlink_to(outside, target_is_directory=True)
            pinned = os.stat(command[-2])
            assert (pinned.st_dev, pinned.st_ino) == (original.st_dev, original.st_ino)
            assert pinned.st_ino != outside.stat().st_ino
            assert "--no-canonicalize" in command
            observed.append(command[-2])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("subprocess.run", mount)
    pin_workspace(root, "checkout", target, original.st_dev, original.st_ino)
    assert len(observed) == 1
    with pytest.raises(FileNotFoundError):
        os.stat(observed[0])


def test_replacement_before_open_is_rejected(tmp_path):
    source = tmp_path / "checkout"
    source.mkdir()
    original = source.stat()
    source.rename(tmp_path / "retained")
    source.mkdir()
    with pytest.raises(ValueError, match="changed"):
        open_checked_workspace(tmp_path, "checkout", original.st_dev, original.st_ino)


@pytest.mark.parametrize("relative", ["../outside", "/outside", ""])
def test_workspace_open_rejects_paths_outside_worker(tmp_path, relative):
    with pytest.raises(ValueError):
        open_checked_workspace(tmp_path, relative, 1, 1)


def test_workspace_open_rejects_symlink_components(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "worker"
    root.mkdir()
    (root / "checkout").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        open_checked_workspace(root, "checkout", outside.stat().st_dev, outside.stat().st_ino)
