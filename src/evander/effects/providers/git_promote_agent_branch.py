from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import subprocess
from typing import Any

from .base import EffectProviderBase


class GitPromoteAgentBranchEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        host_repo_path = self._normalize_optional_string(payload.get("host_repo_path"))
        if not host_repo_path:
            raise ValueError("effect:git:promote-agent-branch requires host_repo_path")
        bare_repo_path = self._normalize_optional_string(payload.get("bare_repo_path"))
        bare_repo_name = self._normalize_optional_string(payload.get("bare_repo_name"))
        if not bare_repo_path and not bare_repo_name:
            raise ValueError(
                "effect:git:promote-agent-branch requires bare_repo_path or bare_repo_name"
            )
        if bare_repo_path and bare_repo_name:
            raise ValueError(
                "effect:git:promote-agent-branch bare_repo_path and bare_repo_name are mutually exclusive"
            )

        agent_branch = self._normalize_optional_string(payload.get("agent_branch"))
        if not agent_branch:
            raise ValueError("effect:git:promote-agent-branch requires agent_branch")

        context = self._context()
        promote = getattr(context.git_branch_promoter, "promote_agent_branch", None)
        if not callable(promote):
            raise RuntimeError("Admin runtime promote_agent_branch is not available")

        result = promote(
            host_repo_path=host_repo_path,
            bare_repo_path=bare_repo_path,
            bare_repo_name=bare_repo_name,
            agent_branch=agent_branch,
            review_branch=self._normalize_optional_string(payload.get("review_branch")),
            target_branch=self._normalize_optional_string(payload.get("target_branch")),
            expected_commit_sha=self._normalize_optional_string(
                payload.get("expected_commit_sha")
            ),
            remote_name=self._normalize_optional_string(payload.get("remote_name")),
        )
        output = self._coerce_result(result)
        output.setdefault("success", False)
        expected_commit = self._normalize_optional_string(payload.get("expected_commit_sha"))
        promoted_commit = self._normalize_optional_string(output.get("commit_sha"))
        if (
            output.get("success") is True
            and expected_commit is not None
            and promoted_commit != expected_commit
        ):
            output["success"] = False
            output["error"] = (
                f"promoted commit {promoted_commit or 'unknown'} did not match "
                f"expected {expected_commit}"
            )
        if output.get("success") is True and self._normalize_bool(
            payload.get("publish_target_to_bare")
        ):
            target_branch = self._normalize_optional_string(payload.get("target_branch"))
            if target_branch is None:
                output["success"] = False
                output["error"] = (
                    "publish_target_to_bare requires an explicit target_branch"
                )
                return output
            if promoted_commit is None:
                output["success"] = False
                output["error"] = (
                    "cannot publish promoted target to bare repo without commit_sha"
                )
                return output
            resolved_bare_repo = self._resolve_bare_repo_path(
                bare_repo_path=bare_repo_path,
                bare_repo_name=bare_repo_name,
            )
            mirror_result = subprocess.run(
                [
                    "git",
                    f"--git-dir={resolved_bare_repo}",
                    "update-ref",
                    f"refs/heads/{target_branch}",
                    promoted_commit,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if mirror_result.returncode != 0:
                output["success"] = False
                output["error"] = self._git_output(mirror_result) or (
                    f"failed to publish {target_branch} to bare repo {resolved_bare_repo}"
                )
                return output
            output["published_target_to_bare"] = True
            output["published_bare_repo_path"] = str(resolved_bare_repo)
        return output

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in (
            "host_repo_path",
            "bare_repo_path",
            "bare_repo_name",
            "agent_branch",
            "review_branch",
            "target_branch",
            "expected_commit_sha",
            "remote_name",
            "publish_target_to_bare",
        ):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("GitPromoteAgentBranchEffectProvider has no runtime context")
        return self.runtime_context

    def _resolve_bare_repo_path(
        self,
        *,
        bare_repo_path: str | None,
        bare_repo_name: str | None,
    ) -> Path:
        if bare_repo_path:
            return Path(bare_repo_path)
        assert bare_repo_name is not None
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
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _normalize_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().casefold() in {"1", "true", "yes", "on"}

    @staticmethod
    def _git_output(result: subprocess.CompletedProcess[str]) -> str:
        return "\n".join(
            part.strip() for part in (result.stderr or "", result.stdout or "") if part.strip()
        )

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
