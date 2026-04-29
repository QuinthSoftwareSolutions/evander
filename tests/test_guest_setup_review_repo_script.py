from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _git(
    repo: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )


def test_guest_setup_review_repo_uses_explicit_base_and_head_refs(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "guest" / "setup_review_repo.sh"

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(origin)], check=True, text=True, capture_output=True
    )

    source = tmp_path / "source"
    subprocess.run(
        ["git", "clone", str(origin), str(source)], check=True, text=True, capture_output=True
    )

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    _git(source, "config", "user.name", "Test User")
    _git(source, "config", "user.email", "test@example.com")

    (source / "README.md").write_text("base\n", encoding="utf-8")
    _git(source, "checkout", "-b", "main")
    _git(source, "add", "README.md", env=env)
    _git(source, "commit", "-m", "Base commit", env=env)
    base_sha = _git(source, "rev-parse", "HEAD", env=env).stdout.strip()
    _git(source, "push", "-u", "origin", "main", env=env)

    (source / "main-only.txt").write_text("target drift\n", encoding="utf-8")
    _git(source, "add", "main-only.txt", env=env)
    _git(source, "commit", "-m", "Target branch drift", env=env)
    target_sha = _git(source, "rev-parse", "HEAD", env=env).stdout.strip()
    _git(source, "push", "origin", "main", env=env)

    _git(source, "checkout", "-b", "review/topic", "main", env=env)
    _git(source, "reset", "--hard", base_sha, env=env)
    (source / "README.md").write_text("head\n", encoding="utf-8")
    (source / "feature.txt").write_text("review\n", encoding="utf-8")
    _git(source, "add", "README.md", "feature.txt", env=env)
    _git(source, "commit", "-m", "Head commit", env=env)
    head_sha = _git(source, "rev-parse", "HEAD", env=env).stdout.strip()
    _git(source, "push", "-u", "origin", "review/topic", env=env)

    prep_repo = tmp_path / "prep"
    subprocess.run(
        ["git", "clone", str(origin), str(prep_repo)], check=True, text=True, capture_output=True
    )
    _git(prep_repo, "checkout", "main", env=env)

    context_file = prep_repo / ".agent-sandbox" / "review-context.env"
    result = subprocess.run(
        [
            "bash",
            str(script_path),
            "--repo-path",
            str(prep_repo),
            "--base-ref",
            "refs/heads/main",
            "--head-ref",
            "refs/heads/review/topic",
            "--context-file",
            str(context_file),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert f"REVIEW_BASE_COMMIT={target_sha}" in result.stdout
    assert f"REVIEW_TARGET_COMMIT={target_sha}" in result.stdout
    assert f"REVIEW_HEAD_COMMIT={head_sha}" in result.stdout
    assert f"REVIEW_DIFF_BASE_COMMIT={base_sha}" in result.stdout
    assert context_file.exists()
    assert _git(prep_repo, "rev-parse", "HEAD", env=env).stdout.strip() == head_sha
    assert (
        _git(prep_repo, "rev-parse", "refs/agent-sandbox/review/base", env=env).stdout.strip()
        == target_sha
    )
    assert (
        _git(prep_repo, "rev-parse", "refs/agent-sandbox/review/head", env=env).stdout.strip()
        == head_sha
    )

    context = context_file.read_text(encoding="utf-8")
    assert "REVIEW_BASE_REF=refs/heads/main" in context
    assert f"REVIEW_BASE_COMMIT={target_sha}" in context
    assert "REVIEW_TARGET_REF=refs/heads/main" in context
    assert f"REVIEW_TARGET_COMMIT={target_sha}" in context
    assert "REVIEW_HEAD_REF=refs/heads/review/topic" in context
    assert f"REVIEW_HEAD_COMMIT={head_sha}" in context
    assert f"REVIEW_DIFF_BASE_COMMIT={base_sha}" in context
    assert f"REVIEW_REPO_PATH={prep_repo}" in context
    assert (
        "main-only.txt"
        in _git(prep_repo, "diff", "--name-only", target_sha, head_sha, env=env).stdout
    )
    assert (
        "main-only.txt"
        not in _git(prep_repo, "diff", "--name-only", base_sha, head_sha, env=env).stdout
    )


def test_guest_setup_review_repo_fetches_missing_explicit_commits(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "guest" / "setup_review_repo.sh"

    upstream = tmp_path / "upstream.git"
    subprocess.run(
        ["git", "init", "--bare", str(upstream)], check=True, text=True, capture_output=True
    )

    source = tmp_path / "source"
    subprocess.run(
        ["git", "clone", str(upstream), str(source)], check=True, text=True, capture_output=True
    )

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    _git(source, "config", "user.name", "Test User")
    _git(source, "config", "user.email", "test@example.com")

    (source / "README.md").write_text("base\n", encoding="utf-8")
    _git(source, "checkout", "-b", "main")
    _git(source, "add", "README.md", env=env)
    _git(source, "commit", "-m", "Base commit", env=env)
    base_sha = _git(source, "rev-parse", "HEAD", env=env).stdout.strip()
    _git(source, "push", "-u", "origin", "main", env=env)

    shallow = tmp_path / "shallow.git"
    subprocess.run(
        ["git", "init", "--bare", str(shallow)], check=True, text=True, capture_output=True
    )
    subprocess.run(
        [
            "git",
            f"--git-dir={shallow}",
            "fetch",
            "--prune",
            str(source),
            "+refs/heads/main:refs/heads/main",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    prep_repo = tmp_path / "prep"
    subprocess.run(
        ["git", "clone", str(shallow), str(prep_repo)], check=True, text=True, capture_output=True
    )
    _git(prep_repo, "checkout", "main", env=env)
    _git(prep_repo, "cat-file", "-e", f"{base_sha}^{{commit}}", env=env)

    _git(source, "checkout", "-b", "review/topic", "main", env=env)
    (source / "README.md").write_text("head\n", encoding="utf-8")
    (source / "feature.txt").write_text("review\n", encoding="utf-8")
    _git(source, "add", "README.md", "feature.txt", env=env)
    _git(source, "commit", "-m", "Head commit", env=env)
    head_sha = _git(source, "rev-parse", "HEAD", env=env).stdout.strip()
    _git(source, "push", "-u", "origin", "review/topic", env=env)

    _git(source, "remote", "add", "shallow", str(shallow))
    _git(source, "push", "shallow", "review/topic", env=env)

    missing_head = subprocess.run(
        ["git", "-C", str(prep_repo), "cat-file", "-e", f"{head_sha}^{{commit}}"],
        text=True,
        capture_output=True,
        env=env,
    )
    assert missing_head.returncode != 0

    result = subprocess.run(
        [
            "bash",
            str(script_path),
            "--repo-path",
            str(prep_repo),
            "--base-ref",
            "refs/heads/main",
            "--head-ref",
            "refs/heads/review/topic",
            "--base-commit",
            base_sha,
            "--head-commit",
            head_sha,
        ],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert f"REVIEW_BASE_COMMIT={base_sha}" in result.stdout
    assert f"REVIEW_HEAD_COMMIT={head_sha}" in result.stdout
    assert f"REVIEW_DIFF_BASE_COMMIT={base_sha}" in result.stdout
    assert _git(prep_repo, "rev-parse", "HEAD", env=env).stdout.strip() == head_sha
    assert (
        _git(prep_repo, "rev-parse", "refs/agent-sandbox/review/base", env=env).stdout.strip()
        == base_sha
    )
    assert (
        _git(prep_repo, "rev-parse", "refs/agent-sandbox/review/head", env=env).stdout.strip()
        == head_sha
    )


def test_guest_setup_review_repo_fails_when_explicit_commit_is_stale(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "guest" / "setup_review_repo.sh"

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(origin)], check=True, text=True, capture_output=True
    )

    source = tmp_path / "source"
    subprocess.run(
        ["git", "clone", str(origin), str(source)], check=True, text=True, capture_output=True
    )

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    _git(source, "config", "user.name", "Test User")
    _git(source, "config", "user.email", "test@example.com")

    (source / "README.md").write_text("base\n", encoding="utf-8")
    _git(source, "checkout", "-b", "main")
    _git(source, "add", "README.md", env=env)
    _git(source, "commit", "-m", "Base commit", env=env)
    stale_base_sha = _git(source, "rev-parse", "HEAD", env=env).stdout.strip()
    _git(source, "push", "-u", "origin", "main", env=env)

    (source / "README.md").write_text("base updated\n", encoding="utf-8")
    _git(source, "add", "README.md", env=env)
    _git(source, "commit", "-m", "Base tip update", env=env)
    base_tip_sha = _git(source, "rev-parse", "HEAD", env=env).stdout.strip()
    _git(source, "push", "origin", "main", env=env)

    _git(source, "checkout", "-b", "review/topic", "main", env=env)
    (source / "feature.txt").write_text("review\n", encoding="utf-8")
    _git(source, "add", "feature.txt", env=env)
    _git(source, "commit", "-m", "Head commit", env=env)
    head_sha = _git(source, "rev-parse", "HEAD", env=env).stdout.strip()
    _git(source, "push", "-u", "origin", "review/topic", env=env)

    prep_repo = tmp_path / "prep"
    subprocess.run(
        ["git", "clone", str(origin), str(prep_repo)], check=True, text=True, capture_output=True
    )
    _git(prep_repo, "checkout", "main", env=env)
    _git(prep_repo, "remote", "add", "agent", str(origin), env=env)

    result = subprocess.run(
        [
            "bash",
            str(script_path),
            "--repo-path",
            str(prep_repo),
            "--base-ref",
            "refs/heads/main",
            "--head-ref",
            "refs/heads/review/topic",
            "--base-commit",
            "0000000000000000000000000000000000000001",
            "--head-commit",
            head_sha,
        ],
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode != 0
    assert (
        "Explicit commit 0000000000000000000000000000000000000001 for refs/heads/main "
        "was not present after fetching agent"
    ) in result.stderr
    assert (
        _git(prep_repo, "rev-parse", "refs/agent-sandbox/review/base", env=env).stdout.strip()
        == base_tip_sha
    )
    head_ref = subprocess.run(
        ["git", "-C", str(prep_repo), "show-ref", "--verify", "refs/agent-sandbox/review/head"],
        text=True,
        capture_output=True,
        env=env,
    )
    assert head_ref.returncode != 0
    assert _git(prep_repo, "rev-parse", "HEAD", env=env).stdout.strip() == base_tip_sha
    assert stale_base_sha != base_tip_sha
    assert base_tip_sha != head_sha


def test_guest_setup_review_repo_help_mentions_explicit_refs(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "guest" / "setup_review_repo.sh"

    text = script_path.read_text(encoding="utf-8")

    assert "show_help()" in text
    assert '== "--help"' in text
    assert "Usage:" in text
    assert "--base-ref REF" in text
    assert "--head-ref REF" in text
