from __future__ import annotations

import subprocess
from pathlib import Path

from evander.pr_merge_preflight import (
    GitMergePreflightEngine,
    MergePreflightPullRequest,
)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Truman Test")
    (repo / "base.txt").write_text("base\n")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-q", "-m", "base")


def _branch_with(
    repo: Path,
    branch: str,
    *,
    source_branch: str = "main",
    files: dict[str, str],
) -> str:
    _git(repo, "checkout", "-q", "-b", branch, source_branch)
    for relative_path, content in files.items():
        file_path = repo / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
    _git(repo, "add", *files.keys())
    _git(repo, "commit", "-q", "-m", branch)
    _git(repo, "checkout", "-q", source_branch)
    return _git(repo, "rev-parse", branch).stdout.strip()


def _pr(
    pr_id: str,
    *,
    source_branch: str,
    target_branch: str,
    source_commit: str | None,
    target_commit: str | None,
) -> MergePreflightPullRequest:
    return MergePreflightPullRequest(
        pr_id=pr_id,
        source_branch=source_branch,
        target_branch=target_branch,
        source_commit=source_commit,
        target_commit=target_commit,
        url=f"https://example.invalid/pr/{pr_id}",
        status="active",
    )


def _assert_repo_clean(repo: Path) -> None:
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    worktree_lines = [
        line
        for line in _git(repo, "worktree", "list", "--porcelain").stdout.splitlines()
        if line.startswith("worktree ")
    ]
    assert len(worktree_lines) == 1


def test_single_preflight_reports_clean_merge(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    source_commit = _branch_with(repo, "feat-single", files={"feature.txt": "feature\n"})
    target_commit = _git(repo, "rev-parse", "main").stdout.strip()
    engine = GitMergePreflightEngine()

    report = engine.run(
        repo,
        mode="single",
        pull_requests=[
            _pr(
                "1",
                source_branch="refs/heads/feat-single",
                target_branch="refs/heads/main",
                source_commit=source_commit,
                target_commit=target_commit,
            )
        ],
    )

    assert report.status == "clean"
    assert report.model_dump(mode="json")["status"] == "clean"
    assert report.checks[0].status == "clean"
    assert report.checks[0].pr_ids == ["1"]
    assert report.checks[0].source_commits == [source_commit]
    assert report.summary_text == "single preflight clean for PRs 1."


def test_pairwise_preflight_reports_clean_for_compatible_prs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    target_commit = _git(repo, "rev-parse", "main").stdout.strip()
    source_commit_a = _branch_with(repo, "feat-a", files={"feature-a.txt": "a\n"})
    source_commit_b = _branch_with(repo, "feat-b", files={"feature-b.txt": "b\n"})
    engine = GitMergePreflightEngine()

    report = engine.run(
        repo,
        mode="pairwise",
        pull_requests=[
            _pr(
                "1",
                source_branch="refs/heads/feat-a",
                target_branch="refs/heads/main",
                source_commit=source_commit_a,
                target_commit=target_commit,
            ),
            _pr(
                "2",
                source_branch="refs/heads/feat-b",
                target_branch="refs/heads/main",
                source_commit=source_commit_b,
                target_commit=target_commit,
            ),
        ],
    )

    assert report.status == "clean"
    assert len(report.checks) == 1
    check = report.checks[0]
    assert check.kind == "pairwise"
    assert check.pr_ids == ["1", "2"]
    assert check.source_commits == [source_commit_a, source_commit_b]
    assert check.status == "clean"
    assert report.summary_text == "pairwise preflight clean for PRs 1, 2."


def test_pairwise_preflight_reports_target_conflict_for_source_compatible_prs(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    source_commit_a = _branch_with(repo, "feat-a", files={"base.txt": "feature a\n"})
    source_commit_b = _branch_with(repo, "feat-b", files={"feature-b.txt": "b\n"})
    _git(repo, "checkout", "-q", "main")
    (repo / "base.txt").write_text("target\n")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-q", "-m", "move target")
    target_commit = _git(repo, "rev-parse", "main").stdout.strip()
    engine = GitMergePreflightEngine()

    report = engine.run(
        repo,
        mode="pairwise",
        pull_requests=[
            _pr(
                "1",
                source_branch="refs/heads/feat-a",
                target_branch="refs/heads/main",
                source_commit=source_commit_a,
                target_commit=target_commit,
            ),
            _pr(
                "2",
                source_branch="refs/heads/feat-b",
                target_branch="refs/heads/main",
                source_commit=source_commit_b,
                target_commit=target_commit,
            ),
        ],
    )

    assert report.status == "conflict"
    assert len(report.checks) == 1
    assert report.checks[0].status == "conflict"
    assert report.checks[0].pr_ids == ["1", "2"]
    assert report.checks[0].source_commits == [source_commit_a, source_commit_b]
    assert report.checks[0].conflicted_paths == ["base.txt"]
    assert report.checks[0].target_commit == target_commit
    _assert_repo_clean(repo)


def test_pairwise_preflight_reports_conflict_and_leaves_repo_clean(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    target_commit = _git(repo, "rev-parse", "main").stdout.strip()
    source_commit_a = _branch_with(repo, "feat-a", files={"conflict.txt": "a\n"})
    source_commit_b = _branch_with(repo, "feat-b", files={"conflict.txt": "b\n"})
    before_main = _git(repo, "rev-parse", "main").stdout.strip()
    before_feat_a = _git(repo, "rev-parse", "feat-a").stdout.strip()
    before_feat_b = _git(repo, "rev-parse", "feat-b").stdout.strip()
    engine = GitMergePreflightEngine()

    report = engine.run(
        repo,
        mode="pairwise",
        pull_requests=[
            _pr(
                "1",
                source_branch="refs/heads/feat-a",
                target_branch="refs/heads/main",
                source_commit=source_commit_a,
                target_commit=target_commit,
            ),
            _pr(
                "2",
                source_branch="refs/heads/feat-b",
                target_branch="refs/heads/main",
                source_commit=source_commit_b,
                target_commit=target_commit,
            ),
        ],
    )

    assert report.status == "conflict"
    assert report.checks[0].status == "conflict"
    assert report.checks[0].conflicted_paths == ["conflict.txt"]
    assert "conflict" in report.summary_text.lower()
    assert report.errors == [report.checks[0].explanation]
    assert _git(repo, "rev-parse", "main").stdout.strip() == before_main
    assert _git(repo, "rev-parse", "feat-a").stdout.strip() == before_feat_a
    assert _git(repo, "rev-parse", "feat-b").stdout.strip() == before_feat_b
    _assert_repo_clean(repo)


def test_pairwise_preflight_checks_every_pr_pair(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    target_commit = _git(repo, "rev-parse", "main").stdout.strip()
    source_commit_a = _branch_with(repo, "feat-a", files={"shared.txt": "a\n"})
    source_commit_b = _branch_with(repo, "feat-b", files={"feature-b.txt": "b\n"})
    source_commit_c = _branch_with(repo, "feat-c", files={"shared.txt": "c\n"})
    engine = GitMergePreflightEngine()

    report = engine.run(
        repo,
        mode="pairwise",
        pull_requests=[
            _pr(
                "1",
                source_branch="refs/heads/feat-a",
                target_branch="refs/heads/main",
                source_commit=source_commit_a,
                target_commit=target_commit,
            ),
            _pr(
                "2",
                source_branch="refs/heads/feat-b",
                target_branch="refs/heads/main",
                source_commit=source_commit_b,
                target_commit=target_commit,
            ),
            _pr(
                "3",
                source_branch="refs/heads/feat-c",
                target_branch="refs/heads/main",
                source_commit=source_commit_c,
                target_commit=target_commit,
            ),
        ],
    )

    assert report.status == "conflict"
    assert [check.pr_ids for check in report.checks] == [["1", "2"], ["1", "3"], ["2", "3"]]
    conflict_checks = [check for check in report.checks if check.status == "conflict"]
    assert len(conflict_checks) == 1
    assert conflict_checks[0].pr_ids == ["1", "3"]
    assert conflict_checks[0].conflicted_paths == ["shared.txt"]
    _assert_repo_clean(repo)


def test_ordered_preflight_reports_failing_step_and_cleans_up(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    target_commit = _git(repo, "rev-parse", "main").stdout.strip()
    source_commit_a = _branch_with(repo, "feat-a", files={"ordered.txt": "a\n"})
    source_commit_b = _branch_with(repo, "feat-b", files={"ordered.txt": "b\n"})
    before_main = _git(repo, "rev-parse", "main").stdout.strip()
    before_feat_a = _git(repo, "rev-parse", "feat-a").stdout.strip()
    before_feat_b = _git(repo, "rev-parse", "feat-b").stdout.strip()
    engine = GitMergePreflightEngine()

    report = engine.run(
        repo,
        mode="ordered",
        pull_requests=[
            _pr(
                "1",
                source_branch="refs/heads/feat-a",
                target_branch="refs/heads/main",
                source_commit=source_commit_a,
                target_commit=target_commit,
            ),
            _pr(
                "2",
                source_branch="refs/heads/feat-b",
                target_branch="refs/heads/main",
                source_commit=source_commit_b,
                target_commit=target_commit,
            ),
        ],
    )

    assert report.status == "conflict"
    assert [check.status for check in report.checks] == ["clean", "conflict"]
    assert report.checks[1].pr_ids == ["1", "2"]
    assert report.checks[1].conflicted_paths == ["ordered.txt"]
    assert report.checks[1].explanation == "merge conflict on ordered.txt"
    assert _git(repo, "rev-parse", "main").stdout.strip() == before_main
    assert _git(repo, "rev-parse", "feat-a").stdout.strip() == before_feat_a
    assert _git(repo, "rev-parse", "feat-b").stdout.strip() == before_feat_b
    _assert_repo_clean(repo)


def test_missing_source_commit_or_ref_reports_missing_ref(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    target_commit = _git(repo, "rev-parse", "main").stdout.strip()
    engine = GitMergePreflightEngine()

    report = engine.run(
        repo,
        mode="single",
        pull_requests=[
            _pr(
                "1",
                source_branch="refs/heads/missing-branch",
                target_branch="refs/heads/main",
                source_commit=None,
                target_commit=target_commit,
            )
        ],
    )

    assert report.status == "missing_ref"
    assert report.checks[0].status == "missing_ref"
    assert "missing ref" in report.checks[0].explanation
    assert report.errors == [report.checks[0].explanation]


def test_stale_target_branch_reports_warning(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    old_target_commit = _git(repo, "rev-parse", "main").stdout.strip()
    source_commit = _branch_with(repo, "feat-stale", files={"stale.txt": "stale\n"})
    _git(repo, "checkout", "-q", "main")
    (repo / "main.txt").write_text("moved\n")
    _git(repo, "add", "main.txt")
    _git(repo, "commit", "-q", "-m", "move target")
    current_target_commit = _git(repo, "rev-parse", "main").stdout.strip()
    engine = GitMergePreflightEngine()

    report = engine.run(
        repo,
        mode="single",
        pull_requests=[
            _pr(
                "1",
                source_branch="refs/heads/feat-stale",
                target_branch="refs/heads/main",
                source_commit=source_commit,
                target_commit=old_target_commit,
            )
        ],
    )

    assert report.status == "stale_target_branch"
    assert report.target_commit == old_target_commit
    assert report.current_target_commit == current_target_commit
    assert report.warnings
    assert "stale_target_branch" in report.warnings[0]
    assert report.summary_text == (
        "single preflight is stale for PRs 1: target moved from "
        f"{old_target_commit} to {current_target_commit}."
    )


def test_non_git_repo_reports_repo_error(tmp_path: Path) -> None:
    repo = tmp_path / "not-a-repo"
    repo.mkdir()
    engine = GitMergePreflightEngine()

    report = engine.run(
        repo,
        mode="single",
        pull_requests=[
            _pr(
                "1",
                source_branch="refs/heads/main",
                target_branch="refs/heads/main",
                source_commit="deadbeef",
                target_commit="feedface",
            )
        ],
    )

    assert report.status == "repo_error"
    assert report.checks == []
    assert report.errors
    assert "git" in report.errors[0].lower() or "repository" in report.errors[0].lower()
