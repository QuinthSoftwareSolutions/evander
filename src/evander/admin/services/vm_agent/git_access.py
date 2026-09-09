from __future__ import annotations

from collections.abc import Mapping
import ipaddress
import os
from pathlib import Path
import shlex
from typing import Any
from urllib.parse import urlparse

from evander.git_ssh_authorized_keys import (
    authorized_keys_path_for_user,
    install_git_read_authorized_key,
    install_git_write_authorized_key,
    remove_git_authorized_key_by_public_key_body,
)
from evander.managed_git_grants import (
    managed_git_grants_from_metadata,
    managed_git_key_name,
    managed_git_ssh_alias,
    normalize_git_access_lifetime,
    normalize_git_access_mode,
    public_managed_git_grant,
    remove_managed_git_grant_metadata,
    upsert_managed_git_grant_metadata,
)
from evander.admin.services.vm_agent.common import process_helpers, vm_helpers


class VmAgentGitAccessMixin:
    def list_managed_git_access(self, target: str) -> list[dict[str, Any]]:
        session = self._resolve_git_access_session(target)
        grants = managed_git_grants_from_metadata(session.metadata)
        return [public_managed_git_grant(grants[key]) for key in sorted(grants)]

    def grant_managed_git_access(
        self,
        target: str,
        managed_repo_id: str,
        *,
        access_mode: str = "read-only",
        lifetime: str = "session",
        vm_user: str = "agent",
    ) -> dict[str, Any]:
        session = self._resolve_git_access_session(target)
        repo_id = str(managed_repo_id or "").strip()
        if not repo_id:
            raise ValueError("managed_repo_id is required")
        mode = normalize_git_access_mode(access_mode, default="read-only")
        normalized_lifetime = normalize_git_access_lifetime(lifetime)
        if normalized_lifetime != "session":
            raise ValueError(
                "standalone repository grants must use session lifetime; "
                "operation lifetime is available on clone operations"
            )
        managed_git = self._deps.managed_git_admin
        if managed_git is None:
            raise RuntimeError("managed Git service is not configured")
        managed_git.clone_info(
            repo_id,
            directory_path=session.directory_path,
            session_id=session.session_id,
        )
        existing_grant = managed_git_grants_from_metadata(session.metadata).get(repo_id)
        expected_backend = (
            "container_worker_vm" if self.is_container_session(session) else "libvirt_vm"
        )
        existing_matches = bool(
            existing_grant is not None
            and normalize_git_access_mode(existing_grant.get("access_mode")) == mode
            and normalize_git_access_lifetime(existing_grant.get("lifetime")) == "session"
            and str(existing_grant.get("execution_backend") or expected_backend) == expected_backend
        )
        if existing_grant is not None and existing_grant.get("legacy_metadata_key"):
            self.revoke_managed_git_access(session.session_id, repo_id)
        elif existing_matches and existing_grant is not None:
            public_key_body = str(existing_grant.get("public_key_body") or "").strip()
            authorized_keys_path = str(existing_grant.get("authorized_keys_path") or "").strip()
            if not public_key_body or not authorized_keys_path:
                raise RuntimeError(
                    "Existing managed Git grant is missing credential reconciliation metadata"
                )
            remove_git_authorized_key_by_public_key_body(
                authorized_keys_path=authorized_keys_path,
                public_key_body=public_key_body,
            )
        elif existing_grant is not None:
            self.revoke_managed_git_access(session.session_id, repo_id)
        bare_repo_path, managed_git_root = managed_git.resolve_internal_repository(
            repo_id,
            session.directory_path,
        )
        issue_setup = self._deps.resolve_issue_setup(
            session.directory_path,
            root_dir=self.project_dir,
        )
        key_name = managed_git_key_name(repo_id)
        git_ssh_alias = managed_git_ssh_alias(issue_setup.git_ssh_alias, repo_id)
        if self.is_container_session(session):
            grant = self._grant_container_managed_git_access(
                session=session,
                repo_id=repo_id,
                mode=mode,
                lifetime=normalized_lifetime,
                bare_repo_path=bare_repo_path,
                managed_git_root=managed_git_root,
                issue_setup=issue_setup,
                key_name=key_name,
                git_ssh_alias=git_ssh_alias,
            )
        else:
            grant = self._grant_vm_managed_git_access(
                session=session,
                repo_id=repo_id,
                mode=mode,
                lifetime=normalized_lifetime,
                managed_git_root=managed_git_root,
                issue_setup=issue_setup,
                key_name=key_name,
                git_ssh_alias=git_ssh_alias,
                vm_user=vm_user,
            )
        try:
            self._record_managed_git_grant(session.session_id, grant)
            managed_git.bind(repo_id, kind="authorized_sessions", binding_id=session.session_id)
        except BaseException as exc:
            cleanup_failures = self._rollback_new_managed_git_grant(
                session=session,
                grant=grant,
            )
            if cleanup_failures:
                exc.add_note(
                    "Managed Git grant rollback was incomplete: " + ", ".join(cleanup_failures)
                )
            raise
        return public_managed_git_grant(grant)

    def revoke_managed_git_access(
        self,
        target: str,
        managed_repo_id: str,
        *,
        remove_guest_key: bool = True,
    ) -> dict[str, Any]:
        session = self._resolve_git_access_session(target)
        repo_id = str(managed_repo_id or "").strip()
        grants = managed_git_grants_from_metadata(session.metadata)
        grant = grants.get(repo_id)
        if grant is None:
            return {"managed_repo_id": repo_id, "revoked": False}
        public_key_body = str(grant.get("public_key_body") or "").strip()
        authorized_keys_path = str(grant.get("authorized_keys_path") or "").strip()
        if public_key_body and authorized_keys_path:
            remove_git_authorized_key_by_public_key_body(
                authorized_keys_path=authorized_keys_path,
                public_key_body=public_key_body,
            )
        key_name = str(grant.get("key_name") or "").strip()
        if not key_name:
            legacy_metadata_key = str(grant.get("legacy_metadata_key") or "").strip()
            if legacy_metadata_key == "managed_git_access":
                key_name = "evander-git"
            elif legacy_metadata_key == "container_git_write_access":
                key_name = "evander-git-write"
            else:
                key_name = managed_git_key_name(repo_id)
        if remove_guest_key:
            if self.is_container_session(session):
                backend = self.container_worker_backend(
                    directory_path=session.directory_path,
                    worker_url=self.container_worker_url_for_target(session=session),
                )
                backend.revoke_git_key(session_id=session.session_id, key_name=key_name)
            elif session.vm_id:
                self._remove_vm_managed_git_key(
                    vm_name=session.vm_id,
                    vm_user="agent",
                    key_name=key_name,
                )
        updated_metadata, _ = remove_managed_git_grant_metadata(
            session.metadata,
            repo_id,
            expected_public_key_body=public_key_body or None,
        )
        self._deps.runtime.session_repository.upsert_session(
            session.model_copy(update={"metadata": updated_metadata})
        )
        managed_git = self._deps.managed_git_admin
        if managed_git is not None:
            managed_git.unbind(
                repo_id,
                kind="authorized_sessions",
                binding_id=session.session_id,
            )
        return public_managed_git_grant(grant) | {"revoked": True}

    def clone_managed_repo_into_agent(
        self,
        target: str,
        managed_repo_id: str,
        *,
        access_mode: str = "read-only",
        lifetime: str = "session",
        vm_user: str = "agent",
    ) -> dict[str, Any]:
        session = self._resolve_git_access_session(target)
        repo_id = str(managed_repo_id or "").strip()
        if not repo_id:
            raise ValueError("managed_repo_id is required")
        mode = normalize_git_access_mode(access_mode, default="read-only")
        normalized_lifetime = normalize_git_access_lifetime(lifetime)
        if self.is_container_session(session):
            from evander.effects.registry import EffectProviderFactory
            from evander.events.provider_models import AgentCloneRepoEffectDefinition
            from evander.provider_runtime.adapters import AdminRuntimeProviderAdapter

            provider_config = getattr(self.runtime, "provider_config", None)
            if provider_config is None:
                raise RuntimeError("provider configuration is unavailable")
            result = (
                EffectProviderFactory(
                    provider_config_resolver=provider_config,
                    admin_adapter=AdminRuntimeProviderAdapter(self),
                )
                .build(
                    AgentCloneRepoEffectDefinition(
                        id=f"managed-git-clone-{session.session_id}",
                        display_name="Clone managed repository",
                        type="effect:agent:clone-repo",
                        session_id=session.session_id,
                        managed_repo_id=repo_id,
                        vm_user=vm_user,
                        git_access_mode=mode,
                        git_access_lifetime=normalized_lifetime,
                    ),
                    directory_path=session.directory_path,
                )
                .execute()
            )
            return dict(result)
        if not session.vm_id:
            raise ValueError(f"Session {session.session_id} has no VM")
        result = self.clone_repo_into_vm(
            vm_name=session.vm_id,
            session_id=session.session_id,
            managed_repo_id=repo_id,
            vm_user=vm_user,
            directory_path=session.directory_path,
            git_access_mode=mode,
            git_access_lifetime=normalized_lifetime,
        )
        if hasattr(result, "model_dump"):
            payload = dict(result.model_dump(mode="python"))
        elif isinstance(result, Mapping):
            payload = dict(result)
        else:
            raise RuntimeError("managed Git clone returned an invalid result")
        if payload.get("success") is False:
            raise RuntimeError(str(payload.get("message") or "managed Git clone did not complete"))
        return payload

    def _resolve_git_access_session(self, target: str):
        normalized = str(target or "").strip()
        if not normalized:
            raise ValueError("session target is required")
        repository = self._deps.runtime.session_repository
        session = repository.get_session(normalized)
        if session is None:
            resolver = getattr(repository, "resolve_session_target", None)
            if callable(resolver):
                session = resolver(normalized)
        if session is None:
            raise ValueError(f"Unknown session target: {normalized}")
        return session

    def _record_managed_git_grant(self, session_id: str, grant: Mapping[str, Any]) -> None:
        repository = self._deps.runtime.session_repository
        session = repository.get_session(session_id)
        if session is None:
            raise RuntimeError(f"Cannot record managed Git access for missing session {session_id}")
        repository.upsert_session(
            session.model_copy(
                update={
                    "metadata": upsert_managed_git_grant_metadata(session.metadata, grant),
                }
            )
        )

    def _rollback_new_managed_git_grant(
        self,
        *,
        session: Any,
        grant: Mapping[str, Any],
    ) -> list[str]:
        failures: list[str] = []
        public_key_body = str(grant.get("public_key_body") or "").strip()
        authorized_keys_path = str(grant.get("authorized_keys_path") or "").strip()
        if public_key_body and authorized_keys_path:
            try:
                remove_git_authorized_key_by_public_key_body(
                    authorized_keys_path=authorized_keys_path,
                    public_key_body=public_key_body,
                )
            except Exception:
                failures.append("host authorization cleanup failed")
        key_name = str(grant.get("key_name") or "").strip()
        if key_name:
            try:
                if self.is_container_session(session):
                    backend = self.container_worker_backend(
                        directory_path=session.directory_path,
                        worker_url=self.container_worker_url_for_target(session=session),
                    )
                    backend.revoke_git_key(session_id=session.session_id, key_name=key_name)
                elif session.vm_id:
                    self._remove_vm_managed_git_key(
                        vm_name=session.vm_id,
                        vm_user="agent",
                        key_name=key_name,
                    )
            except Exception:
                failures.append("guest key cleanup failed")
        repository = self._deps.runtime.session_repository
        current = repository.get_session(session.session_id)
        if current is not None:
            try:
                metadata, _ = remove_managed_git_grant_metadata(
                    current.metadata,
                    str(grant.get("managed_repo_id") or ""),
                    expected_public_key_body=public_key_body or None,
                )
                repository.upsert_session(current.model_copy(update={"metadata": metadata}))
            except Exception:
                failures.append("session metadata cleanup failed")
        managed_git = self._deps.managed_git_admin
        if managed_git is not None:
            try:
                managed_git.unbind(
                    str(grant.get("managed_repo_id") or ""),
                    kind="authorized_sessions",
                    binding_id=session.session_id,
                )
            except Exception:
                failures.append("repository binding cleanup failed")
        return failures

    def _grant_container_managed_git_access(
        self,
        *,
        session: Any,
        repo_id: str,
        mode: str,
        lifetime: str,
        bare_repo_path: Path,
        managed_git_root: Path,
        issue_setup: Any,
        key_name: str,
        git_ssh_alias: str,
    ) -> dict[str, Any]:
        backend = self.container_worker_backend(
            directory_path=session.directory_path,
            worker_url=self.container_worker_url_for_target(session=session),
        )
        backend.prepare_session(
            session_id=session.session_id,
            metadata={
                "execution_backend": "container_worker_vm",
                "directory_path": session.directory_path,
            },
        )
        key_record = backend.provision_git_write_key(
            session_id=session.session_id,
            git_host=issue_setup.git_host,
            git_user=issue_setup.git_user,
            git_ssh_alias=git_ssh_alias,
            key_name=key_name,
            alias_is_exact=True,
        )
        config = self._deps.runtime.provider_config.resolve_for_session_id(session.session_id)
        allowed_from = self._container_git_allowed_from(config)
        common_args = {
            "public_key": str(key_record.public_key),
            "git_root": managed_git_root,
            "authorized_repo_path": bare_repo_path,
            "allowed_from": allowed_from,
            "wrapper_path": self.project_dir / "scripts" / "git_ssh_command_wrapper.sh",
            "authorized_keys_path": authorized_keys_path_for_user(issue_setup.git_user),
        }
        try:
            if mode == "write":
                installed = install_git_write_authorized_key(
                    **common_args,
                    session_id=session.session_id,
                )
            else:
                installed = install_git_read_authorized_key(**common_args)
        except BaseException:
            backend.revoke_git_key(session_id=session.session_id, key_name=key_name)
            raise
        grant: dict[str, Any] = {
            "managed_repo_id": repo_id,
            "access_mode": mode,
            "lifetime": lifetime,
            "execution_backend": "container_worker_vm",
            "public_key_body": installed.public_key_body,
            "authorized_keys_path": str(installed.authorized_keys_path),
            "git_user": issue_setup.git_user,
            "git_ssh_alias": key_record.git_ssh_alias,
            "key_name": key_name,
            "allowed_from": installed.allowed_from,
        }
        if mode == "write":
            grant["authorized_ref_prefix"] = installed.authorized_ref_prefix
            grant["authorized_pr_notes_ref"] = installed.authorized_pr_notes_ref
        return grant

    def _grant_vm_managed_git_access(
        self,
        *,
        session: Any,
        repo_id: str,
        mode: str,
        lifetime: str,
        managed_git_root: Path,
        issue_setup: Any,
        key_name: str,
        git_ssh_alias: str,
        vm_user: str,
    ) -> dict[str, Any]:
        if not session.vm_id:
            raise ValueError(f"Session {session.session_id} has no VM")
        completed = process_helpers.run_subprocess(
            [str(self.project_dir / "scripts" / "bootstrap_git_vm.sh"), session.vm_id, vm_user],
            capture_output=True,
            text=True,
            env=os.environ
            | {
                "EVANDER_GIT_ROOT": str(managed_git_root),
                "EVANDER_GIT_HOST": issue_setup.git_host,
                "EVANDER_GIT_USER": issue_setup.git_user,
                "EVANDER_GIT_SSH_ALIAS": git_ssh_alias,
                "EVANDER_GIT_KEY_NAME": key_name,
                "EVANDER_GIT_REPO": repo_id,
                "EVANDER_GIT_SESSION_ID": session.session_id,
                "EVANDER_GIT_READ_ONLY": "1" if mode == "read-only" else "0",
            },
        )
        if completed.returncode != 0:
            error = RuntimeError(
                (completed.stderr or completed.stdout or "").strip()
                or f"Failed to provision managed Git access in {session.vm_id}"
            )
            try:
                self._remove_vm_managed_git_key(
                    vm_name=session.vm_id,
                    vm_user=vm_user,
                    key_name=key_name,
                )
            except Exception:
                error.add_note("Managed Git bootstrap key cleanup failed")
            raise error
        fields: dict[str, str] = {}
        for line in (completed.stdout or "").splitlines():
            name, separator, value = line.partition("=")
            if separator:
                fields[name] = value.strip()
        public_key_body = fields.get("EVANDER_GIT_PUBLIC_KEY_BODY", "")
        authorized_keys_path = fields.get("EVANDER_GIT_AUTHORIZED_KEYS_PATH", "")
        if not public_key_body or not authorized_keys_path:
            raise RuntimeError("Git bootstrap returned incomplete access metadata")
        return {
            "managed_repo_id": repo_id,
            "access_mode": mode,
            "lifetime": lifetime,
            "execution_backend": "libvirt_vm",
            "public_key_body": public_key_body,
            "authorized_keys_path": authorized_keys_path,
            "git_user": issue_setup.git_user,
            "git_ssh_alias": git_ssh_alias,
            "key_name": key_name,
        }

    def _remove_vm_managed_git_key(
        self,
        *,
        vm_name: str,
        vm_user: str,
        key_name: str,
    ) -> None:
        vm_ip = vm_helpers.resolve_vm_ip(vm_name)
        private_key = vm_helpers.resolve_private_key_path()
        if not vm_ip or private_key is None:
            raise RuntimeError(f"Unable to resolve SSH access for {vm_name}")
        script = f"""
set -euo pipefail
KEY_NAME={shlex.quote(key_name)}
rm -f "$HOME/.ssh/$KEY_NAME" "$HOME/.ssh/$KEY_NAME.pub"
python3 - "$HOME/.ssh/config" "$KEY_NAME" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
key_name = sys.argv[2]
if not path.exists():
    raise SystemExit(0)
text = path.read_text(encoding="utf-8")
markers = [(f"# BEGIN evander git grant {{key_name}}", f"# END evander git grant {{key_name}}")]
if key_name == "evander-git":
    markers.append(("# BEGIN evander git host", "# END evander git host"))
elif key_name == "evander-git-write":
    markers.append(("# BEGIN evander git write host", "# END evander git write host"))
for begin, end in markers:
    while begin in text and end in text:
        start = text.index(begin)
        stop = text.index(end, start) + len(end)
        suffix = "\n" if stop < len(text) and text[stop:stop + 1] == "\n" else ""
        text = text[:start].rstrip() + "\n" + text[stop + len(suffix):].lstrip("\n")
path.write_text(text, encoding="utf-8")
PY
"""
        completed = process_helpers.run_subprocess(
            [
                "ssh",
                *vm_helpers.ssh_options(vm_name, vm_ip, private_key),
                f"{vm_user}@{vm_ip}",
                "bash",
                "-s",
            ],
            capture_output=True,
            text=True,
            timeout=60.0,
            input=script,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "").strip()
                or f"Failed to remove managed Git key from {vm_name}"
            )

    @staticmethod
    def _container_git_allowed_from(config: Any) -> str:
        configured = str(getattr(config, "container_worker_git_allowed_from", None) or "").strip()
        if configured:
            return configured
        base_url = str(getattr(config, "container_worker_base_url", None) or "").strip()
        host = urlparse(base_url).hostname if base_url else None
        if not host:
            raise ValueError(
                "container_worker_git_allowed_from or container_worker_base_url is required"
            )
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return host
        return f"{address}/{32 if address.version == 4 else 128}"
