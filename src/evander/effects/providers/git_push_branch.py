from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import subprocess
import tempfile
from typing import Any
from uuid import uuid4

from ...admin.services.git_helpers import resolve_canonical_origin_url_for_repo
from .base import EffectProviderBase


class GitPushBranchEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        host_repo_path = self._normalize_optional_string(payload.get("host_repo_path"))
        if not host_repo_path:
            raise ValueError("effect:git:push-branch requires host_repo_path")
        local_branch = self._normalize_optional_string(payload.get("local_branch"))
        if not local_branch:
            raise ValueError("effect:git:push-branch requires local_branch")
        origin_branch = self._normalize_optional_string(payload.get("origin_branch"))
        if not origin_branch:
            raise ValueError("effect:git:push-branch requires origin_branch")
        bare_repo_path = self._normalize_optional_string(payload.get("bare_repo_path"))
        bare_repo_name = self._normalize_optional_string(payload.get("bare_repo_name"))
        if bare_repo_path and bare_repo_name:
            raise ValueError(
                "effect:git:push-branch bare_repo_path and bare_repo_name are mutually exclusive"
            )

        context = self._context()
        push_branch = getattr(context.git_branch_pusher, "push_branch", None)
        if not callable(push_branch):
            raise RuntimeError("Admin runtime push_branch is not available")

        source_bare_repo = self._resolve_bare_repo_path(
            bare_repo_path=bare_repo_path,
            bare_repo_name=bare_repo_name,
        )
        if source_bare_repo is not None:
            return self._push_from_bare_repo(
                push_branch=push_branch,
                trusted_host_repo_path=host_repo_path,
                bare_repo_path=source_bare_repo,
                local_branch=local_branch,
                origin_branch=origin_branch,
                canonical_origin_url=self._normalize_optional_string(
                    payload.get("canonical_origin_url")
                ),
                payload=payload,
            )
        return self._push(
            push_branch=push_branch,
            source_repo_path=host_repo_path,
            local_branch=local_branch,
            origin_branch=origin_branch,
            payload=payload,
        )

    def _push_from_bare_repo(
        self,
        *,
        push_branch: Any,
        trusted_host_repo_path: str,
        bare_repo_path: Path,
        local_branch: str,
        origin_branch: str,
        canonical_origin_url: str | None,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not bare_repo_path.exists():
            return self._source_failure(
                trusted_host_repo_path=trusted_host_repo_path,
                bare_repo_path=bare_repo_path,
                local_branch=local_branch,
                origin_branch=origin_branch,
                message=f"bare repo not found: {bare_repo_path}",
            )
        canonical_origin_url = canonical_origin_url or self._normalize_optional_string(
            resolve_canonical_origin_url_for_repo(trusted_host_repo_path)
        )
        if canonical_origin_url is None:
            return self._canonical_origin_failure(
                trusted_host_repo_path=trusted_host_repo_path,
                bare_repo_path=bare_repo_path,
                local_branch=local_branch,
                origin_branch=origin_branch,
            )
        branch_name = self._normalize_branch_ref(local_branch)
        with tempfile.TemporaryDirectory(prefix="evander-git-push-") as tmp:
            source_repo_path = Path(tmp) / "source"
            clone = subprocess.run(
                [
                    "git",
                    "clone",
                    "--no-checkout",
                    "--no-tags",
                    str(bare_repo_path),
                    str(source_repo_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if clone.returncode != 0:
                return self._source_failure(
                    trusted_host_repo_path=trusted_host_repo_path,
                    bare_repo_path=bare_repo_path,
                    local_branch=local_branch,
                    origin_branch=origin_branch,
                    message=self._git_output(clone) or "failed to clone bare repo",
                )
            source_local_branch = f"evander-push-{uuid4().hex}"
            fetch = subprocess.run(
                [
                    "git",
                    "-C",
                    str(source_repo_path),
                    "fetch",
                    "--no-tags",
                    "origin",
                    f"+refs/heads/{branch_name}:refs/heads/{source_local_branch}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            source_ref_repository = "bare_repo"
            if fetch.returncode != 0:
                bare_error = self._git_output(fetch)
                fetch = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(source_repo_path),
                        "fetch",
                        "--no-tags",
                        trusted_host_repo_path,
                        f"+refs/heads/{branch_name}:refs/heads/{source_local_branch}",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                source_ref_repository = "trusted_host_repo"
                if fetch.returncode != 0:
                    host_error = self._git_output(fetch)
                    message = "\n".join(
                        part
                        for part in (
                            f"bare mirror: {bare_error}" if bare_error else "",
                            f"trusted host repo: {host_error}" if host_error else "",
                        )
                        if part
                    ) or (
                        f"failed to resolve {branch_name} from {bare_repo_path} "
                        f"or {trusted_host_repo_path}"
                    )
                    return self._source_failure(
                        trusted_host_repo_path=trusted_host_repo_path,
                        bare_repo_path=bare_repo_path,
                        local_branch=local_branch,
                        origin_branch=origin_branch,
                        message=message,
                    )
            output = self._push(
                push_branch=push_branch,
                source_repo_path=str(source_repo_path),
                local_branch=source_local_branch,
                origin_branch=origin_branch,
                canonical_origin_url=canonical_origin_url,
                payload=payload,
            )
            output["local_branch"] = branch_name
            output["source_local_branch"] = source_local_branch
            output["source_bare_repo_path"] = str(bare_repo_path)
            output["trusted_host_repo_path"] = trusted_host_repo_path
            output["source_ref_repository"] = source_ref_repository
            output["canonical_origin_url"] = canonical_origin_url
            return output

    def _push(
        self,
        *,
        push_branch: Any,
        source_repo_path: str,
        local_branch: str,
        origin_branch: str,
        payload: Mapping[str, Any],
        canonical_origin_url: str | None = None,
    ) -> dict[str, Any]:
        expected_commit_sha = self._normalize_optional_string(
            payload.get("expected_commit_sha")
        )
        if expected_commit_sha is not None:
            resolved = subprocess.run(
                ["git", "-C", source_repo_path, "rev-parse", local_branch],
                capture_output=True,
                text=True,
                check=False,
            )
            actual_commit_sha = resolved.stdout.strip() if resolved.returncode == 0 else None
            if actual_commit_sha != expected_commit_sha:
                message = (
                    f"ref {local_branch} resolved to {actual_commit_sha or 'missing'}, "
                    f"expected approved commit {expected_commit_sha}"
                )
                return {
                    "success": False,
                    "action": "git_push_branch",
                    "preflight_success": False,
                    "reason": "source_commit_mismatch",
                    "message": message,
                    "error": message,
                    "local_branch": local_branch,
                    "origin_branch": origin_branch,
                    "expected_commit_sha": expected_commit_sha,
                    "actual_commit_sha": actual_commit_sha,
                }
        result = push_branch(
            host_repo_path=source_repo_path,
            local_branch=local_branch,
            origin_branch=origin_branch,
            canonical_origin_url=canonical_origin_url
            or self._normalize_optional_string(payload.get("canonical_origin_url")),
            provider=self._normalize_optional_string(payload.get("provider")),
            base_branch=self._normalize_optional_string(payload.get("base_branch")),
            session_id=self._normalize_optional_string(payload.get("session_id")),
            work_item_id=self._normalize_optional_string(payload.get("work_item_id")),
        )
        output = self._coerce_result(result)
        output.setdefault("success", False)
        if output["success"] is False and not self._normalize_optional_string(
            output.get("error")
        ):
            message = self._normalize_optional_string(output.get("message"))
            if message is not None:
                output["error"] = message
        return output

    def _resolve_bare_repo_path(
        self,
        *,
        bare_repo_path: str | None,
        bare_repo_name: str | None,
    ) -> Path | None:
        if bare_repo_path:
            return Path(bare_repo_path)
        if not bare_repo_name:
            return None
        normalized_name = (
            bare_repo_name if bare_repo_name.endswith(".git") else f"{bare_repo_name}.git"
        )
        context = self._context()
        root_value = context.env.get("EVANDER_GIT_ROOT") or context.provider_config.get(
            "git_root"
        )
        root = (
            Path(str(root_value))
            if root_value
            else Path(__file__).resolve().parents[4] / "git-remotes"
        )
        if not root.is_absolute():
            root = Path(__file__).resolve().parents[4] / root
        return root / normalized_name

    @staticmethod
    def _normalize_branch_ref(value: str) -> str:
        prefix = "refs/heads/"
        return value[len(prefix) :] if value.startswith(prefix) else value

    @staticmethod
    def _git_output(result: subprocess.CompletedProcess[str]) -> str:
        return "\n".join(
            part.strip() for part in (result.stderr or "", result.stdout or "") if part.strip()
        )

    @staticmethod
    def _source_failure(
        *,
        trusted_host_repo_path: str,
        bare_repo_path: Path,
        local_branch: str,
        origin_branch: str,
        message: str,
    ) -> dict[str, Any]:
        return {
            "success": False,
            "action": "git_push_branch",
            "preflight_success": False,
            "reason": "source_ref_missing",
            "message": message,
            "error": message,
            "host_repo_path": trusted_host_repo_path,
            "source_bare_repo_path": str(bare_repo_path),
            "local_branch": local_branch,
            "origin_branch": origin_branch,
        }

    @staticmethod
    def _canonical_origin_failure(
        *,
        trusted_host_repo_path: str,
        bare_repo_path: Path,
        local_branch: str,
        origin_branch: str,
    ) -> dict[str, Any]:
        message = (
            "canonical origin URL is not configured and could not be resolved "
            f"from trusted checkout {trusted_host_repo_path}"
        )
        return {
            "success": False,
            "action": "git_push_branch",
            "preflight_success": False,
            "reason": "canonical_origin_missing",
            "message": message,
            "error": message,
            "host_repo_path": trusted_host_repo_path,
            "source_bare_repo_path": str(bare_repo_path),
            "local_branch": local_branch,
            "origin_branch": origin_branch,
        }

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in (
            "host_repo_path",
            "bare_repo_path",
            "bare_repo_name",
            "local_branch",
            "expected_commit_sha",
            "origin_branch",
            "canonical_origin_url",
            "provider",
            "base_branch",
            "session_id",
            "work_item_id",
        ):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("GitPushBranchEffectProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _coerce_result(result: Any) -> dict[str, Any]:
        if result is None:
            return {}
        if isinstance(result, Mapping):
            return dict(result)
        model_dump = getattr(result, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, Mapping):
                return dict(dumped)
        if hasattr(result, "__dict__"):
            return dict(vars(result))
        return {"result": result}
