from __future__ import annotations

import subprocess
from pathlib import Path

from evander.git_pr_notes import agent_pr_notes_ref_for_session


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "guest" / "agent_pr_note.sh"


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _run_helper(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["bash", str(HELPER), *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_agent_pr_note_helper_uses_configured_per_session_ref_and_file_input(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    notes_ref = agent_pr_notes_ref_for_session("session-1")
    _git(repo, "config", "evander.prNotesRef", notes_ref)

    (repo / "hello.txt").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "hello.txt")
    _git(repo, "commit", "-m", "Initial")

    initial = tmp_path / "initial.md"
    initial.write_text("Initial summary\n", encoding="utf-8")
    _run_helper(repo, "set", "-F", str(initial))

    assert _run_helper(repo, "show") == "Initial summary"
    assert _git(repo, "notes", f"--ref={notes_ref}", "show", "HEAD") == "Initial summary"

    (repo / "hello.txt").write_text("hello again\n", encoding="utf-8")
    _git(repo, "add", "hello.txt")
    _git(repo, "commit", "-m", "Update")
    _run_helper(repo, "ensure-head")
    assert _git(repo, "notes", f"--ref={notes_ref}", "show", "HEAD") == "Initial summary"

    follow_up = tmp_path / "follow-up.md"
    follow_up.write_text("Follow-up summary\n", encoding="utf-8")
    _run_helper(repo, "append", "-F", str(follow_up))

    expected = "Initial summary\n\nFollow-up summary"
    assert _run_helper(repo, "show") == expected
    assert _git(repo, "notes", f"--ref={notes_ref}", "show", "HEAD") == expected
