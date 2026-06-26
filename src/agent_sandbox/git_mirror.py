from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import fcntl
import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote


@dataclass(frozen=True)
class ContainerGitMirror:
    original_repo_source: str
    canonical_repo_source: str
    resolved_repo_path: str
    repo_name: str
    mirror_name: str
    mirror_path: Path
    worker_repo_source: str


class ContainerGitMirrorResolver:
    def __init__(
        self,
        *,
        project_dir: Path,
        git_root: str | Path,
        git_host: str,
        git_user: str,
        git_ssh_alias: str,
        env: dict[str, str] | None = None,
    ) -> None:
        self.project_dir = project_dir
        self.git_root_path = self._absolute_git_root(Path(git_root))
        self.git_host = str(git_host or "").strip() or "192.168.250.1"
        self.git_user = str(git_user or "").strip() or os.environ.get("USER", "agent")
        self.git_ssh_alias = str(git_ssh_alias or "").strip() or "agent-sandbox-git-host"
        self.env = dict(env or os.environ)

    def resolve(
        self,
        *,
        repo_source: str,
        canonical_repo_source: str | None = None,
        bare_repo_name: str | None = None,
        bare_repo_path: str | None = None,
        clone_branch: str | None = None,
        directory_path: str | None = None,
        session_id: str | None = None,
    ) -> ContainerGitMirror:
        del directory_path, session_id
        original_repo_source = repo_source.strip()
        if not original_repo_source:
            raise ValueError("repo_source is required")

        resolved_repo_path = self._resolve_repo_path(original_repo_source)
        raw_repo_name = self._resolve_repo_name(original_repo_source)
        repo_name = _safe_worker_repo_name(raw_repo_name)
        explicit_mirror_target = self._explicit_mirror_target(
            bare_repo_name=bare_repo_name,
            bare_repo_path=bare_repo_path,
        )
        explicit_project_mirror = explicit_mirror_target is not None
        if explicit_mirror_target is None:
            canonical_hash_source = (
                original_repo_source
                if _is_remote_git_source(original_repo_source)
                else str(Path(resolved_repo_path).resolve(strict=False))
            )
            mirror_name = _mirror_name(raw_repo_name, canonical_hash_source)
            mirror_path = self.git_root_path / f"{mirror_name}.git"
        else:
            mirror_name, mirror_path = explicit_mirror_target
        preferred_canonical_repo_source = _optional_text(canonical_repo_source)
        resolved_canonical_repo_source = preferred_canonical_repo_source or self._canonical_repo_source(
            original_repo_source=original_repo_source,
            resolved_repo_path=resolved_repo_path,
        )

        with self._mirror_initialization_lock(mirror_name):
            self._ensure_bare_remote(mirror_name)
            with self._mirror_sync_lock(mirror_path):
                self._refresh_mirror(
                    mirror_path=mirror_path,
                    resolved_repo_path=resolved_repo_path,
                    original_repo_source=original_repo_source,
                    canonical_repo_source=resolved_canonical_repo_source,
                    preferred_remote_url=preferred_canonical_repo_source,
                    prune_stale_branches=not explicit_project_mirror,
                )
                if clone_branch:
                    self._ensure_branch_exists(mirror_path, clone_branch)

        return ContainerGitMirror(
            original_repo_source=original_repo_source,
            canonical_repo_source=resolved_canonical_repo_source,
            resolved_repo_path=resolved_repo_path,
            repo_name=repo_name,
            mirror_name=mirror_name,
            mirror_path=mirror_path,
            worker_repo_source=f"ssh://{self.git_ssh_alias}{mirror_path}",
        )

    def _absolute_git_root(self, git_root: Path) -> Path:
        if git_root.is_absolute():
            return git_root
        return (self.project_dir / git_root).resolve(strict=False)

    def _script_path(self, name: str) -> Path:
        path = self.project_dir / "scripts" / name
        if not path.exists():
            raise RuntimeError(f"Missing helper script: {path}")
        return path

    def _helper_env(self) -> dict[str, str]:
        return self.env | {
            "AGENT_SANDBOX_GIT_ROOT": str(self.git_root_path),
            "AGENT_SANDBOX_GIT_HOST": self.git_host,
            "AGENT_SANDBOX_GIT_USER": self.git_user,
            "AGENT_SANDBOX_GIT_SSH_ALIAS": self.git_ssh_alias,
        }

    def _run(
        self,
        cmd: list[str],
        *,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            cmd,
            cwd=self.project_dir,
            env=env or self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(message or f"Command failed: {' '.join(cmd)}")
        return completed

    def _resolve_repo_path(self, repo_source: str) -> str:
        completed = self._run(
            [str(self._script_path("resolve_repo_source.sh")), "--path-only", repo_source],
            env=self._helper_env(),
        )
        return (completed.stdout or "").strip()

    def _resolve_repo_name(self, repo_source: str) -> str:
        completed = self._run(
            [str(self._script_path("resolve_repo_source.sh")), "--name-only", repo_source],
            env=self._helper_env(),
        )
        name = (completed.stdout or "").strip()
        if not name:
            raise RuntimeError(f"Could not determine repository name for {repo_source}")
        return name

    def _ensure_bare_remote(self, mirror_name: str) -> None:
        self._run(
            [str(self._script_path("create_git_remote.sh")), mirror_name, "agents"],
            env=self._helper_env(),
        )

    def _explicit_mirror_target(
        self,
        *,
        bare_repo_name: str | None,
        bare_repo_path: str | None,
    ) -> tuple[str, Path] | None:
        normalized_name = _optional_text(bare_repo_name)
        normalized_path = _optional_text(bare_repo_path)
        if normalized_name and normalized_path:
            raise ValueError("bare_repo_name and bare_repo_path are mutually exclusive")
        if normalized_name:
            mirror_name = _normalize_bare_repo_name(normalized_name)
            return mirror_name, self.git_root_path / f"{mirror_name}.git"
        if not normalized_path:
            return None

        mirror_path = Path(normalized_path)
        if not mirror_path.is_absolute():
            mirror_path = self.git_root_path / mirror_path
        mirror_path = mirror_path.resolve(strict=False)
        git_root = self.git_root_path.resolve(strict=False)
        if mirror_path.parent != git_root:
            raise ValueError("bare_repo_path must resolve to a direct child of git_root")
        if not mirror_path.name.endswith(".git"):
            raise ValueError("bare_repo_path must point to a .git bare repository")
        mirror_name = _normalize_bare_repo_name(mirror_path.name)
        return mirror_name, mirror_path

    @contextmanager
    def _mirror_initialization_lock(self, mirror_name: str) -> Iterator[None]:
        self.git_root_path.mkdir(parents=True, exist_ok=True)
        with self._exclusive_lock(self.git_root_path / f".{mirror_name}.lock"):
            yield

    @contextmanager
    def _mirror_sync_lock(self, mirror_path: Path) -> Iterator[None]:
        with self._exclusive_lock(mirror_path / "agent-sandbox-sync.lock"):
            yield

    @contextmanager
    def _exclusive_lock(self, path: Path) -> Iterator[None]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as handle:
            self._repair_path_ownership_if_root(path, recursive=False)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _refresh_mirror(
        self,
        *,
        mirror_path: Path,
        resolved_repo_path: str,
        original_repo_source: str,
        canonical_repo_source: str,
        preferred_remote_url: str | None = None,
        prune_stale_branches: bool = True,
    ) -> None:
        remote_name = _mirror_remote_name_for_repo(
            self,
            resolved_repo_path,
            preferred_remote_url=preferred_remote_url,
        )
        if not _is_remote_git_source(original_repo_source):
            _sync_and_validate_local_remote(self, resolved_repo_path, remote_name)
        refspecs = _mirror_fetch_refspecs(self, resolved_repo_path, remote_name)
        if not refspecs:
            raise RuntimeError(f"No refs found to mirror from {resolved_repo_path}")
        self._run(
            [
                "git",
                f"--git-dir={mirror_path}",
                "fetch",
                "--prune",
                resolved_repo_path,
                *refspecs,
            ]
        )
        if prune_stale_branches:
            _prune_stale_mirror_branches(self, mirror_path, refspecs)
        mirror_source = (
            original_repo_source
            if _is_remote_git_source(original_repo_source)
            else str(Path(resolved_repo_path).resolve(strict=False))
        )
        mirror_sync_remote = "1" if _git_remote_url(self, resolved_repo_path, remote_name) else "0"
        for key, value in (
            ("agent-sandbox.trustedRepoPath", str(Path(resolved_repo_path).resolve(strict=False))),
            ("agent-sandbox.mirrorSource", mirror_source),
            ("agent-sandbox.mirrorRemoteName", remote_name),
            ("agent-sandbox.mirrorSyncRemote", mirror_sync_remote),
            ("agent-sandbox.mirrorForceLocal", "0"),
            ("agent-sandbox.canonicalOriginUrl", canonical_repo_source),
        ):
            if value:
                self._run(["git", f"--git-dir={mirror_path}", "config", key, value])
        default_base_branch = _default_base_branch_for_repo_remote(
            self, resolved_repo_path, remote_name
        )
        if default_base_branch:
            self._run(
                [
                    "git",
                    f"--git-dir={mirror_path}",
                    "config",
                    "agent-sandbox.baseBranch",
                    default_base_branch,
                ]
            )
            self._run(
                [
                    "git",
                    f"--git-dir={mirror_path}",
                    "symbolic-ref",
                    "HEAD",
                    f"refs/heads/{default_base_branch}",
                ]
            )
        self._repair_mirror_ownership_if_root(mirror_path)

    def _repair_mirror_ownership_if_root(self, mirror_path: Path) -> None:
        self._repair_path_ownership_if_root(mirror_path, recursive=True)

    def _repair_path_ownership_if_root(self, path: Path, *, recursive: bool) -> None:
        if os.geteuid() != 0 or not self.git_user or self.git_user == "root":
            return
        group_result = self._run(["id", "-gn", self.git_user])
        target_group = group_result.stdout.strip()
        if not target_group:
            raise RuntimeError(f"Cannot resolve group for Git remote owner {self.git_user!r}")
        cmd = ["chown"]
        if recursive:
            cmd.append("-R")
        cmd.extend([f"{self.git_user}:{target_group}", str(path)])
        self._run(cmd)

    def _canonical_repo_source(
        self,
        *,
        original_repo_source: str,
        resolved_repo_path: str,
    ) -> str:
        if _is_remote_git_source(original_repo_source):
            return original_repo_source
        remote_name = _mirror_remote_name_for_repo(self, resolved_repo_path)
        canonical = _resolve_canonical_origin_url_for_repo(self, resolved_repo_path, remote_name)
        return canonical or str(Path(resolved_repo_path).resolve(strict=False))

    def _ensure_branch_exists(self, mirror_path: Path, clone_branch: str) -> None:
        normalized = clone_branch.strip()
        if not normalized:
            return
        self._run(["git", "check-ref-format", "--branch", normalized])
        completed = subprocess.run(
            [
                "git",
                f"--git-dir={mirror_path}",
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{normalized}",
            ],
            cwd=self.project_dir,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Requested clone_branch {normalized!r} is not present in {mirror_path}"
            )


def _is_remote_git_source(source: str) -> bool:
    text = str(source or "").strip()
    if not text:
        return False
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        return True
    return bool(re.match(r"^[^/@:\s]+@[^:\s]+:.+", text))


def _mirror_name(repo_name: str, canonical_source: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", repo_name.strip()).strip(".-")
    if safe_name.endswith(".git"):
        safe_name = safe_name[:-4]
    safe_name = safe_name or "repo"
    source_hash = hashlib.sha256(canonical_source.encode("utf-8")).hexdigest()[:12]
    return f"{safe_name}-{source_hash}"


def _normalize_bare_repo_name(value: str) -> str:
    text = value.strip()
    if "/" in text or "\\" in text or text in {"", ".", ".."}:
        raise ValueError("bare_repo_name must be a single repository name")
    if not re.fullmatch(r"[A-Za-z0-9._-]+(?:\.git)?", text):
        raise ValueError("bare_repo_name contains unsupported characters")
    name = text[:-4] if text.endswith(".git") else text
    if name in {"", ".", ".."}:
        raise ValueError("bare_repo_name must be a single repository name")
    return name


def _safe_worker_repo_name(repo_name: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", unquote(repo_name.strip())).strip(".-")
    if not safe_name or safe_name in {".", ".."}:
        return "repo"
    return safe_name


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _git_remote_url(
    resolver: ContainerGitMirrorResolver,
    repo_path: str,
    remote_name: str,
) -> str:
    completed = subprocess.run(
        ["git", "-C", repo_path, "remote", "get-url", remote_name],
        cwd=resolver.project_dir,
        env=resolver.env,
        capture_output=True,
        text=True,
        check=False,
    )
    return (completed.stdout or "").strip() if completed.returncode == 0 else ""


def _mirror_remote_name_for_repo(
    resolver: ContainerGitMirrorResolver,
    repo_path: str,
    *,
    preferred_remote_url: str | None = None,
) -> str:
    preferred = _optional_text(preferred_remote_url)
    if preferred:
        remotes = _git_output(resolver, ["git", "-C", repo_path, "remote"])
        for line in remotes.splitlines():
            remote = line.strip()
            if remote and _git_remote_url(resolver, repo_path, remote) == preferred:
                return remote

    current_branch = _git_output(resolver, ["git", "-C", repo_path, "branch", "--show-current"])
    if current_branch:
        tracking_remote = _git_output(
            resolver,
            ["git", "-C", repo_path, "config", "--get", f"branch.{current_branch}.remote"],
        )
        if tracking_remote:
            return tracking_remote

    remote_heads = _git_output(
        resolver,
        ["git", "-C", repo_path, "for-each-ref", "--format=%(refname)", "refs/remotes/*/HEAD"],
    )
    for line in remote_heads.splitlines():
        parts = line.strip().split("/")
        if len(parts) >= 4 and parts[:2] == ["refs", "remotes"] and parts[-1] == "HEAD":
            return parts[2]

    remotes = _git_output(resolver, ["git", "-C", repo_path, "remote"])
    for line in remotes.splitlines():
        remote = line.strip()
        if remote:
            return remote
    return "origin"


def _default_base_branch_for_repo_remote(
    resolver: ContainerGitMirrorResolver,
    repo_path: str,
    remote_name: str,
) -> str | None:
    for cmd in (
        [
            "git",
            "-C",
            repo_path,
            "symbolic-ref",
            "--quiet",
            "--short",
            f"refs/remotes/{remote_name}/HEAD",
        ],
        ["git", "-C", repo_path, "branch", "--show-current"],
    ):
        value = _git_output(resolver, cmd)
        if not value:
            continue
        remote_prefix = f"{remote_name}/"
        if value.startswith(remote_prefix):
            value = value[len(remote_prefix) :]
        return value or None
    return None


def _resolve_canonical_origin_url_for_repo(
    resolver: ContainerGitMirrorResolver,
    repo_path: str,
    remote_name: str,
) -> str:
    remote_url = _git_remote_url(resolver, repo_path, remote_name)
    if not remote_url:
        return ""
    try:
        resolved_remote = str(Path(remote_url).resolve(strict=False))
    except OSError:
        resolved_remote = remote_url
    if "/runtime/repo-cache/" in resolved_remote and resolved_remote.endswith(".git"):
        mirror_origin = _git_output(
            resolver, ["git", f"--git-dir={resolved_remote}", "remote", "get-url", "origin"]
        )
        if mirror_origin:
            return mirror_origin
    return remote_url


def _sync_and_validate_local_remote(
    resolver: ContainerGitMirrorResolver,
    repo_path: str,
    remote_name: str,
) -> None:
    if not remote_name or not _git_remote_url(resolver, repo_path, remote_name):
        return

    resolver._run(["git", "-C", repo_path, "fetch", "--prune", remote_name])
    _refuse_stale_local_branches(resolver, repo_path, remote_name)


def _refuse_stale_local_branches(
    resolver: ContainerGitMirrorResolver,
    repo_path: str,
    remote_name: str,
) -> None:
    remote_prefix = f"refs/remotes/{remote_name}/"
    for local_ref in _git_refs(resolver, repo_path, "refs/heads"):
        branch = local_ref.removeprefix("refs/heads/")
        if _skip_mirror_branch(branch):
            continue

        remote_ref = f"{remote_prefix}{branch}"
        if not _git_ref_exists(resolver, repo_path, remote_ref):
            continue

        counts = _git_output(
            resolver,
            [
                "git",
                "-C",
                repo_path,
                "rev-list",
                "--left-right",
                "--count",
                f"{local_ref}...{remote_ref}",
            ],
        )
        count_parts = counts.split()
        if len(count_parts) != 2:
            continue

        local_ahead, remote_ahead = (int(count_parts[0]), int(count_parts[1]))
        if remote_ahead <= 0:
            continue

        raise RuntimeError(
            "Refusing to mirror stale local refs from "
            f"{repo_path}; local {branch} is behind {remote_name}/{branch} "
            f"(ahead={local_ahead} behind={remote_ahead})."
        )


def _mirror_fetch_refspecs(
    resolver: ContainerGitMirrorResolver,
    repo_path: str,
    remote_name: str,
) -> list[str]:
    refspecs: list[str] = []
    mirrored_branches: set[str] = set()
    for ref in _git_refs(resolver, repo_path, "refs/heads"):
        branch = ref.removeprefix("refs/heads/")
        if _skip_mirror_branch(branch):
            continue
        mirrored_branches.add(branch)
        refspecs.append(f"+{ref}:refs/heads/{branch}")
    if remote_name:
        remote_prefix = f"refs/remotes/{remote_name}/"
        for ref in _git_refs(resolver, repo_path, f"refs/remotes/{remote_name}"):
            branch = ref.removeprefix(remote_prefix)
            if branch == "HEAD" or _skip_mirror_branch(branch) or branch in mirrored_branches:
                continue
            mirrored_branches.add(branch)
            refspecs.append(f"+{ref}:refs/heads/{branch}")
    for ref in _git_refs(resolver, repo_path, "refs/tags"):
        tag = ref.removeprefix("refs/tags/")
        if tag:
            refspecs.append(f"+{ref}:refs/tags/{tag}")
    return refspecs


def _prune_stale_mirror_branches(
    resolver: ContainerGitMirrorResolver,
    mirror_path: Path,
    refspecs: list[str],
) -> None:
    expected_heads = {
        destination_ref
        for destination_ref in (_refspec_destination_ref(refspec) for refspec in refspecs)
        if destination_ref.startswith("refs/heads/")
    }

    for ref in _git_bare_refs(resolver, mirror_path, "refs/heads"):
        branch = ref.removeprefix("refs/heads/")
        if ref in expected_heads or _skip_mirror_branch(branch):
            continue
        resolver._run(["git", f"--git-dir={mirror_path}", "update-ref", "-d", ref])


def _refspec_destination_ref(refspec: str) -> str:
    normalized = refspec.removeprefix("+")
    if ":" not in normalized:
        return ""
    return normalized.split(":", 1)[1].strip()


def _skip_mirror_branch(branch: str) -> bool:
    return (
        branch.startswith("agents/")
        or branch.startswith("review/")
        or branch.startswith("team/")
    )


def _git_bare_refs(
    resolver: ContainerGitMirrorResolver,
    bare_repo_path: Path,
    namespace: str,
) -> list[str]:
    output = _git_output(
        resolver,
        ["git", f"--git-dir={bare_repo_path}", "for-each-ref", "--format=%(refname)", namespace],
    )
    return [line.strip() for line in output.splitlines() if line.strip()]


def _git_refs(
    resolver: ContainerGitMirrorResolver,
    repo_path: str,
    namespace: str,
) -> list[str]:
    output = _git_output(
        resolver, ["git", "-C", repo_path, "for-each-ref", "--format=%(refname)", namespace]
    )
    return [line.strip() for line in output.splitlines() if line.strip()]


def _git_ref_exists(
    resolver: ContainerGitMirrorResolver,
    repo_path: str,
    ref: str,
) -> bool:
    completed = subprocess.run(
        ["git", "-C", repo_path, "show-ref", "--verify", "--quiet", ref],
        cwd=resolver.project_dir,
        env=resolver.env,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def _git_output(resolver: ContainerGitMirrorResolver, cmd: list[str]) -> str:
    completed = subprocess.run(
        cmd,
        cwd=resolver.project_dir,
        env=resolver.env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return ""
    return (completed.stdout or "").strip()
