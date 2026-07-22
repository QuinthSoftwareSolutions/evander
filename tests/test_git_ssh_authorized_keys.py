from __future__ import annotations

import threading
import time

import pytest

import evander.git_ssh_authorized_keys as git_keys
from evander.git_ssh_authorized_keys import (
    install_git_read_authorized_key,
    install_git_write_authorized_key,
    remove_git_authorized_key_by_public_key_body,
)


PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestAuthorizedKey test-key"


def test_install_git_read_authorized_key_scopes_exact_repository(tmp_path) -> None:
    git_root = tmp_path / "git-remotes"
    repo = git_root / "repo.git"
    repo.mkdir(parents=True)
    wrapper = tmp_path / "scripts" / "git_ssh_command_wrapper.sh"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    authorized_keys = tmp_path / "authorized_keys"

    record = install_git_read_authorized_key(
        public_key=PUBLIC_KEY,
        git_root=git_root,
        authorized_repo_path=repo,
        allowed_from="127.0.0.1",
        wrapper_path=wrapper,
        authorized_keys_path=authorized_keys,
    )

    text = authorized_keys.read_text(encoding="utf-8")
    assert record.public_key_body == "AAAAC3NzaC1lZDI1NTE5AAAAITestAuthorizedKey"
    assert record.authorized_repo_path == repo
    assert 'from="127.0.0.1"' in text
    assert "EVANDER_GIT_ROOT_B64=" in text
    assert "EVANDER_GIT_AUTH_ACCESS=read-only" in text
    assert "EVANDER_GIT_AUTH_REPO_B64=" in text
    assert "EVANDER_GIT_REFRESH_REQUIRED=1" in text
    assert "EVANDER_GIT_AUTH_REPO_PREFIX_B64=" not in text
    assert str(wrapper) in text


def test_install_git_write_authorized_key_scopes_repo_and_session(tmp_path) -> None:
    git_root = tmp_path / "git-remotes"
    repo = git_root / "repo.git"
    repo.mkdir(parents=True)
    wrapper = tmp_path / "scripts" / "git_ssh_command_wrapper.sh"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    authorized_keys = tmp_path / "authorized_keys"

    record = install_git_write_authorized_key(
        public_key=PUBLIC_KEY,
        git_root=git_root,
        authorized_repo_path=repo,
        session_id="session-1",
        allowed_from="10.2.1.4/32",
        wrapper_path=wrapper,
        authorized_keys_path=authorized_keys,
    )

    text = authorized_keys.read_text(encoding="utf-8")
    assert record.public_key_body == "AAAAC3NzaC1lZDI1NTE5AAAAITestAuthorizedKey"
    assert record.authorized_ref_prefix == "refs/heads/agents/session-1/"
    assert record.authorized_pr_notes_ref == "refs/notes/agent-pr/84097828fc31"
    assert 'from="10.2.1.4/32"' in text
    assert "EVANDER_GIT_AUTH_ACCESS=write" in text
    assert "EVANDER_GIT_AUTH_REPO_B64=" in text
    assert "EVANDER_GIT_REFRESH_REQUIRED=1" in text
    assert "EVANDER_GIT_AUTH_SESSION_ID_B64=" in text
    assert "EVANDER_GIT_AUTH_REF_PREFIX_B64=" in text
    assert "EVANDER_GIT_AUTH_PR_NOTES_REF_B64=" in text
    assert str(wrapper) in text

    assert remove_git_authorized_key_by_public_key_body(
        authorized_keys_path=authorized_keys,
        public_key_body=record.public_key_body,
    )
    assert authorized_keys.read_text(encoding="utf-8") == ""


def test_install_git_write_authorized_key_does_not_embed_configured_secrets_env_file(
    tmp_path, monkeypatch
) -> None:
    git_root = tmp_path / "git-remotes"
    repo = git_root / "repo.git"
    repo.mkdir(parents=True)
    wrapper = tmp_path / "scripts" / "git_ssh_command_wrapper.sh"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    authorized_keys = tmp_path / "authorized_keys"
    secrets_env_file = tmp_path / "local-secrets.env"
    monkeypatch.setenv("EVANDER_SECRETS_ENV_FILE", str(secrets_env_file))

    record = install_git_write_authorized_key(
        public_key=PUBLIC_KEY,
        git_root=git_root,
        authorized_repo_path=repo,
        session_id="session-1",
        allowed_from="10.2.1.4/32",
        wrapper_path=wrapper,
        authorized_keys_path=authorized_keys,
    )

    text = authorized_keys.read_text(encoding="utf-8")
    assert record.public_key_body == "AAAAC3NzaC1lZDI1NTE5AAAAITestAuthorizedKey"
    assert "EVANDER_SECRETS_ENV_FILE" not in text
    assert str(secrets_env_file) not in text


def test_install_git_write_authorized_key_rejects_repo_outside_git_root(tmp_path) -> None:
    wrapper = tmp_path / "git_ssh_command_wrapper.sh"
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    with pytest.raises(ValueError, match="authorized repo must be under Git root"):
        install_git_write_authorized_key(
            public_key=PUBLIC_KEY,
            git_root=tmp_path / "git-remotes",
            authorized_repo_path=tmp_path / "other" / "repo.git",
            session_id="session-1",
            allowed_from="10.2.1.4/32",
            wrapper_path=wrapper,
            authorized_keys_path=tmp_path / "authorized_keys",
        )


def test_install_git_write_authorized_key_rejects_multiline_public_key(tmp_path) -> None:
    git_root = tmp_path / "git-remotes"
    repo = git_root / "repo.git"
    repo.mkdir(parents=True)
    wrapper = tmp_path / "git_ssh_command_wrapper.sh"
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    authorized_keys = tmp_path / "authorized_keys"
    authorized_keys.write_text(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKeepMe keep\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="single line"):
        install_git_write_authorized_key(
            public_key=(
                f"{PUBLIC_KEY}\nssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIInjected unrestricted"
            ),
            git_root=git_root,
            authorized_repo_path=repo,
            session_id="session-1",
            allowed_from="10.2.1.4/32",
            wrapper_path=wrapper,
            authorized_keys_path=authorized_keys,
        )

    assert authorized_keys.read_text(encoding="utf-8") == (
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKeepMe keep\n"
    )


def test_install_git_write_authorized_key_rejects_path_session_id(tmp_path) -> None:
    git_root = tmp_path / "git-remotes"
    repo = git_root / "repo.git"
    repo.mkdir(parents=True)
    wrapper = tmp_path / "git_ssh_command_wrapper.sh"
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    with pytest.raises(ValueError, match="session_id must be a single"):
        install_git_write_authorized_key(
            public_key=PUBLIC_KEY,
            git_root=git_root,
            authorized_repo_path=repo,
            session_id="session/1",
            allowed_from="10.2.1.4/32",
            wrapper_path=wrapper,
            authorized_keys_path=tmp_path / "authorized_keys",
        )


def test_parallel_git_write_key_installs_preserve_both_entries(tmp_path, monkeypatch) -> None:
    git_root = tmp_path / "git-remotes"
    repo = git_root / "repo.git"
    repo.mkdir(parents=True)
    wrapper = tmp_path / "git_ssh_command_wrapper.sh"
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    authorized_keys = tmp_path / "authorized_keys"
    original_write_authorized_keys = git_keys._write_authorized_keys

    def slow_write_authorized_keys(path, lines):
        time.sleep(0.05)
        original_write_authorized_keys(path, lines)

    monkeypatch.setattr(git_keys, "_write_authorized_keys", slow_write_authorized_keys)
    start = threading.Barrier(3)
    errors: list[BaseException] = []

    def install(public_key: str, session_id: str) -> None:
        try:
            start.wait(timeout=5)
            install_git_write_authorized_key(
                public_key=public_key,
                git_root=git_root,
                authorized_repo_path=repo,
                session_id=session_id,
                allowed_from="10.2.1.4/32",
                wrapper_path=wrapper,
                authorized_keys_path=authorized_keys,
            )
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(
            target=install,
            args=(
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestParallelKeyOne key-1",
                "session-1",
            ),
        ),
        threading.Thread(
            target=install,
            args=(
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestParallelKeyTwo key-2",
                "session-2",
            ),
        ),
    ]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    text = authorized_keys.read_text(encoding="utf-8")
    assert "AAAAC3NzaC1lZDI1NTE5AAAAITestParallelKeyOne" in text
    assert "AAAAC3NzaC1lZDI1NTE5AAAAITestParallelKeyTwo" in text
