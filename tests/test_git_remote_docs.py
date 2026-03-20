from pathlib import Path


def test_git_remote_docs_and_script_are_present() -> None:
    readme = Path("/home/jonathan/agent-sandbox/README.md").read_text(encoding="utf-8")
    ops = Path("/home/jonathan/agent-sandbox/docs/operations.md").read_text(encoding="utf-8")
    git_docs = Path("/home/jonathan/agent-sandbox/docs/git-remotes.md").read_text(encoding="utf-8")
    script = Path("/home/jonathan/agent-sandbox/scripts/create_git_remote.sh").read_text(
        encoding="utf-8"
    )

    assert "create_git_remote.sh" in readme
    assert "create_git_remote.sh" in ops
    assert "refs/heads/agents/" in git_docs
    assert "git init --bare" in script
