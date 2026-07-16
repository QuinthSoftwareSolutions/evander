from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_managed_repo_operator_flow_is_documented_without_legacy_helpers() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    ops = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    git_remotes = (ROOT / "docs" / "git-remotes.md").read_text(encoding="utf-8")

    for document in (readme, ops, git_remotes):
        assert "git-repos register" in document
        assert "managed_repo_id" in document
    for legacy_helper in (
        "mirror_issue_repo.sh",
        "setup_issue_git_vm.sh",
        "promote_agent_branch.sh",
        "open_agent_pr.sh",
    ):
        assert legacy_helper not in git_remotes
    assert "effect:git:promote-agent-branch" in git_remotes
    assert "effect:pr:create" in git_remotes
