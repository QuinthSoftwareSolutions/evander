from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from evander.config import GatewayConfig
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.git_push_branch import GitPushBranchEffectProvider


class _AdminServiceStub:
    def __init__(self, result: dict[str, object] | None = None) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []
        self.resolved_local_commits: list[str] = []

    def push_branch(self, **kwargs):
        self.calls.append(kwargs)
        repo_path = Path(str(kwargs["host_repo_path"]))
        if (repo_path / ".git").exists():
            resolved = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo_path),
                    "rev-parse",
                    "--verify",
                    f"{kwargs['local_branch']}^{{commit}}",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.resolved_local_commits.append(resolved.stdout.strip())
        if self.result is None:
            raise RuntimeError("result not configured")
        return dict(self.result)


def _build_context(tmp_path: Path, admin_service: _AdminServiceStub) -> EffectRuntimeContext:
    return EffectRuntimeContext(
        resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
        admin_service=admin_service,
        env={},
    )


def test_git_push_branch_provider_calls_admin_service_with_normalized_arguments(
    tmp_path: Path,
) -> None:
    admin_service = _AdminServiceStub(
        result={
            "success": True,
            "action": "git_push_branch",
            "message": "Pushed branch",
            "local_branch": "review/session-1/work",
            "origin_branch": "feature/topic",
        }
    )
    provider = GitPushBranchEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-push",
            display_name="Push branch",
            type="effect:git:push-branch",
            host_repo_path=" /trusted/repo ",
            local_branch=" review/session-1/work ",
            origin_branch=" refs/heads/feature/default ",
            canonical_origin_url=" git@ssh.dev.azure.com:v3/org/project/repo ",
            provider=" azure_devops ",
            base_branch=" refs/heads/main ",
            session_id=" session-1 ",
            work_item_id=" 123 ",
        ),
        runtime_context=_build_context(tmp_path, admin_service),
    )

    result = provider.execute(resolved_config={"origin_branch": " feature/topic "})

    assert admin_service.calls == [
        {
            "host_repo_path": "/trusted/repo",
            "local_branch": "review/session-1/work",
            "origin_branch": "feature/topic",
            "canonical_origin_url": "git@ssh.dev.azure.com:v3/org/project/repo",
            "provider": "azure_devops",
            "base_branch": "refs/heads/main",
            "session_id": "session-1",
            "work_item_id": "123",
        }
    ]
    assert result["success"] is True
    assert result["origin_branch"] == "feature/topic"


def test_git_push_branch_provider_requires_admin_service_method(tmp_path: Path) -> None:
    provider = GitPushBranchEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-push",
            display_name="Push branch",
            type="effect:git:push-branch",
            host_repo_path="/trusted/repo",
            local_branch="review/session-1/work",
            origin_branch="feature/topic",
        ),
        runtime_context=EffectRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
            admin_service=SimpleNamespace(),
            env={},
        ),
    )

    with pytest.raises(RuntimeError, match="Admin runtime push_branch is not available"):
        provider.execute()


def test_git_push_branch_provider_uses_failure_message_as_error(tmp_path: Path) -> None:
    admin_service = _AdminServiceStub(
        result={
            "success": False,
            "reason": "push_rejected",
            "message": "Canonical remote rejected the push.",
        }
    )
    provider = GitPushBranchEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-push-failure",
            display_name="Push branch",
            type="effect:git:push-branch",
            host_repo_path="/trusted/repo",
            local_branch="release/release-1",
            origin_branch="release/release-1",
        ),
        runtime_context=_build_context(tmp_path, admin_service),
    )

    result = provider.execute()

    assert result["success"] is False
    assert result["error"] == "Canonical remote rejected the push."


def test_git_push_branch_provider_pushes_from_bare_repo_without_touching_host_checkout(
    tmp_path: Path,
) -> None:
    trusted_repo = tmp_path / "trusted"
    bare_repo = tmp_path / "source.git"
    subprocess.run(
        ["git", "init", "-b", "main", str(trusted_repo)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(trusted_repo), "config", "user.name", "Test User"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(trusted_repo), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    (trusted_repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(trusted_repo), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(trusted_repo), "commit", "-m", "base"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(trusted_repo), "checkout", "-b", "release/release-1"],
        check=True,
        capture_output=True,
    )
    (trusted_repo / "release.txt").write_text("release\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(trusted_repo), "add", "release.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(trusted_repo), "commit", "-m", "release"],
        check=True,
        capture_output=True,
    )
    release_commit = subprocess.run(
        ["git", "-C", str(trusted_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(trusted_repo), "checkout", "main"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "clone", "--bare", str(trusted_repo), str(bare_repo)],
        check=True,
        capture_output=True,
    )
    before_head = subprocess.run(
        ["git", "-C", str(trusted_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    admin_service = _AdminServiceStub(
        result={"success": True, "destination_commit": release_commit}
    )
    provider = GitPushBranchEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-push-bare",
            display_name="Push release from bare repo",
            type="effect:git:push-branch",
            host_repo_path=str(trusted_repo),
            bare_repo_path=str(bare_repo),
            local_branch="release/release-1",
            origin_branch="release/release-1",
            canonical_origin_url="https://example.invalid/repo.git",
            provider="azure_devops",
        ),
        runtime_context=_build_context(tmp_path, admin_service),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["source_bare_repo_path"] == str(bare_repo)
    assert result["trusted_host_repo_path"] == str(trusted_repo)
    assert result["source_ref_repository"] == "bare_repo"
    assert admin_service.resolved_local_commits == [release_commit]
    assert admin_service.calls[0]["host_repo_path"] != str(trusted_repo)
    assert subprocess.run(
        ["git", "-C", str(trusted_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == before_head
    assert subprocess.run(
        ["git", "-C", str(trusted_repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout == ""

    subprocess.run(
        [
            "git",
            f"--git-dir={bare_repo}",
            "update-ref",
            "-d",
            "refs/heads/release/release-1",
        ],
        check=True,
    )
    fallback_result = provider.execute()

    assert fallback_result["success"] is True
    assert fallback_result["source_ref_repository"] == "trusted_host_repo"
    assert admin_service.resolved_local_commits == [release_commit, release_commit]
    assert admin_service.calls[1]["host_repo_path"] != str(trusted_repo)


def test_git_push_branch_provider_uses_trusted_origin_and_non_head_source_ref(
    tmp_path: Path,
) -> None:
    trusted_repo = tmp_path / "trusted"
    source_bare = tmp_path / "source.git"
    canonical_bare = tmp_path / "canonical.git"
    subprocess.run(
        ["git", "init", "-b", "main", str(trusted_repo)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(trusted_repo), "config", "user.name", "Test User"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(trusted_repo), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    (trusted_repo / "README.md").write_text("main\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(trusted_repo), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(trusted_repo), "commit", "-m", "main"],
        check=True,
        capture_output=True,
        text=True,
    )
    main_commit = subprocess.run(
        ["git", "-C", str(trusted_repo), "rev-parse", "main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "clone", "--bare", str(trusted_repo), str(source_bare)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "init", "--bare", str(canonical_bare)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(trusted_repo), "remote", "add", "origin", str(canonical_bare)],
        check=True,
    )

    admin_service = _AdminServiceStub(
        result={"success": True, "local_commit": main_commit}
    )
    provider = GitPushBranchEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-push-main",
            display_name="Push main from mirror",
            type="effect:git:push-branch",
            host_repo_path=str(trusted_repo),
            bare_repo_path=str(source_bare),
            local_branch="main",
            origin_branch="release/main",
        ),
        runtime_context=_build_context(tmp_path, admin_service),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["canonical_origin_url"] == str(canonical_bare)
    assert result["local_branch"] == "main"
    assert result["source_local_branch"].startswith("evander-push-")
    assert admin_service.calls[0]["canonical_origin_url"] == str(canonical_bare)
    assert admin_service.calls[0]["local_branch"] == result["source_local_branch"]
    assert admin_service.resolved_local_commits == [main_commit]


def test_git_push_branch_provider_reports_missing_bare_repo(tmp_path: Path) -> None:
    admin_service = _AdminServiceStub(result={"success": True})
    provider = GitPushBranchEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-push-missing-bare",
            display_name="Push missing release",
            type="effect:git:push-branch",
            host_repo_path="/trusted/repo",
            bare_repo_path=str(tmp_path / "missing.git"),
            local_branch="release/release-1",
            origin_branch="release/release-1",
        ),
        runtime_context=_build_context(tmp_path, admin_service),
    )

    result = provider.execute()

    assert result["success"] is False
    assert result["reason"] == "source_ref_missing"
    assert "bare repo not found" in result["error"]
    assert admin_service.calls == []
