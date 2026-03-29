from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_issue_mirror_helper_is_documented() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    ops = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    git_remotes = (ROOT / "docs" / "git-remotes.md").read_text(encoding="utf-8")

    assert "mirror_issue_repo.sh" in readme
    assert "mirror_issue_repo.sh" in ops
    assert "mirror_issue_repo.sh" in git_remotes
    assert "bootstrap_git_vm.sh" in git_remotes
    assert "setup_issue_git_vm.sh" in readme
    assert "setup_issue_git_vm.sh" in git_remotes
    assert "promote_agent_branch.sh" in git_remotes
    assert "open_agent_pr.sh" in git_remotes
