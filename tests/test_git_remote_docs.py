from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_managed_git_repository_docs_and_credential_script_are_present() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    ops = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    git_docs = (ROOT / "docs" / "git-remotes.md").read_text(encoding="utf-8")
    askpass = (ROOT / "scripts" / "managed_git_askpass.sh").read_text(encoding="utf-8")

    for document in (readme, ops, git_docs):
        assert "managed_repo_id" in document
        assert "git-repos register" in document
    assert "git-repos migrate --dry-run" in readme
    assert "git-repos migrate --apply" in git_docs
    assert "git-repos migrate --finalize" in git_docs
    assert "post-receive" in git_docs
    assert "configured PAT" in git_docs
    assert "refs/heads/agents/" in git_docs
    assert "review/" in git_docs
    assert "expected_commit_sha" in git_docs
    assert "runtime/git-worktrees/<managed_repo_id>/<operation-id>" in git_docs
    assert "EVANDER_GIT_PASSWORD" in askpass
