"""Authorize and submit exact guest commits through the existing sandbox adapters."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import logging
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from evander.admin.services.managed_git_common import ManagedGitNotFoundError
from evander.git_pr_notes import agent_pr_notes_ref_for_session
from evander.git_refs import logical_branch_name, private_branch_ref
from evander.git_submissions import GitSubmissionStore
from evander.managed_git_grants import managed_git_grants_from_metadata
from evander.models import ToolName
from evander.policy import PolicyError

LOGGER = logging.getLogger(__name__)


def blocked(code: str, message: str, **details) -> dict:
    return {
        "success": False,
        "code": code,
        "message": message,
        "retryable": code in {"transport_failure", "concurrent_update", "submission_busy"},
        **details,
    }


class GitSubmissionService:
    def __init__(self, runtime, managed_git, project_dir, *, execute, validators=None):
        self.runtime = runtime
        self.managed_git = managed_git
        self.project_dir = Path(project_dir)
        self.store = GitSubmissionStore(managed_git.runtime_dir)
        self.execute = execute
        self.validators = validators or {}

    def register_checkout(self, **kwargs) -> str:
        return self.store.register(**kwargs)

    def resolve(self, session, *, managed_repo_id=None, checkout_id=None) -> dict:
        grants = managed_git_grants_from_metadata(session.metadata)
        candidates = []
        insufficient_access = False
        for binding in self.store.list(session.session_id):
            if managed_repo_id and binding["managed_repo_id"] != managed_repo_id:
                continue
            if checkout_id and binding["checkout_id"] != checkout_id:
                continue
            owner = binding["owner_kind"]
            if owner != "session":
                validator = self.validators.get(owner)
                if validator is None or not validator(binding):
                    self.store.retire(binding["checkout_id"])
                    continue
            grant = grants.get(binding["managed_repo_id"], {})
            if grant.get("access_mode") != "write" or grant.get("lifetime", "session") != "session":
                insufficient_access = True
                continue
            candidates.append(binding)
        if len(candidates) != 1:
            return blocked(
                ("insufficient_access" if insufficient_access else "checkout_unavailable")
                if not candidates
                else "checkout_ambiguous",
                "Select one prepared checkout with an active writable Git grant.",
                checkouts=[
                    {k: item[k] for k in ("checkout_id", "managed_repo_id")}
                    | {"repository_name": item.get("repository_name") or item["managed_repo_id"]}
                    for item in candidates
                ],
            )
        binding = candidates[0]
        # Validate directory ownership against the canonical registry before guest execution.
        try:
            self.managed_git.clone_info(
                binding["managed_repo_id"], session.directory_path, session_id=session.session_id
            )
        except ManagedGitNotFoundError:
            return blocked(
                "checkout_unavailable",
                "The repository is no longer available in this session's directory; reconcile preparation.",
            )
        return {"success": True, "binding": binding}

    def submit(
        self,
        session,
        *,
        expected_commit_sha,
        summary_markdown,
        managed_repo_id=None,
        checkout_id=None,
        authorize=None,
    ) -> dict:
        sha = str(expected_commit_sha).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
            return blocked("invalid_commit", "expected_commit_sha must be a full Git commit SHA.")
        if not isinstance(summary_markdown, str) or not summary_markdown.strip():
            return blocked("summary_required", "Provide a complete nonempty Markdown summary.")
        if len(summary_markdown.encode("utf-8")) > 32768:
            return blocked(
                "summary_too_large", "The complete summary must fit within 32 KiB of UTF-8 text."
            )
        resolved = self.resolve(session, managed_repo_id=managed_repo_id, checkout_id=checkout_id)
        if not resolved["success"]:
            return resolved
        binding = resolved["binding"]
        repo_id = binding["managed_repo_id"]
        if authorize:
            authorize(
                {
                    "managed_repo_id": repo_id,
                    "checkout_id": binding["checkout_id"],
                    "expected_commit_sha": sha,
                    "summary_markdown": summary_markdown,
                }
            )
        lock_root = self.store.path.parent / "managed-git-locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(f"{session.session_id}:{repo_id}".encode()).hexdigest()
        with (lock_root / f"submit-{key}.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return blocked(
                    "submission_busy",
                    "A submission is already in progress; retry the same request.",
                    retryable=True,
                )
            # Resolve current session/grant and owner state again after waiting for a writer.
            try:
                fresh = self.runtime.policy.require_tool(
                    session.session_id, ToolName.SUBMIT_GIT_CHANGES
                )
            except PolicyError:
                return blocked(
                    "insufficient_access",
                    "The session or submission capability is no longer available.",
                )
            resolved = self.resolve(
                fresh, managed_repo_id=repo_id, checkout_id=binding["checkout_id"]
            )
            if not resolved["success"]:
                return resolved
            binding = resolved["binding"]
            if authorize:
                authorize(
                    {
                        "managed_repo_id": repo_id,
                        "checkout_id": binding["checkout_id"],
                        "expected_commit_sha": sha,
                        "summary_markdown": summary_markdown,
                    }
                )
            grant = managed_git_grants_from_metadata(fresh.metadata)[repo_id]
            info = self.managed_git.clone_info(
                repo_id, fresh.directory_path, session_id=fresh.session_id
            )
            alias = grant.get("git_ssh_alias", "")
            if not re.fullmatch(r"[A-Za-z0-9._-]+", alias):
                return blocked(
                    "git_access_unavailable", "The managed SSH grant needs reconciliation."
                )
            parsed = urlsplit(info["clone_url"])
            remote = urlunsplit(parsed._replace(netloc=alias))
            request = dict(
                binding,
                expected_commit_sha=sha,
                summary_markdown=summary_markdown,
                remote_url=remote,
                notes_ref=agent_pr_notes_ref_for_session(fresh.session_id),
            )
            encoded = base64.b64encode(json.dumps(request, ensure_ascii=False).encode()).decode()
            helper = (self.project_dir / "scripts/guest/push_agent.sh").read_text(encoding="utf-8")
            command = ["bash", "-c", helper, "evander-submit", "--submit-json", encoded]
            try:
                # Start from the stable agent home so the helper can distinguish a
                # missing checkout from an unavailable execution transport.
                execution = self.execute(fresh, command, "/home/agent")
                lines = str(execution.get("stdout") or "").splitlines()
                payloads = [
                    line.removeprefix("EVANDER_GIT_SUBMISSION_RESULT=")
                    for line in lines
                    if line.startswith("EVANDER_GIT_SUBMISSION_RESULT=")
                ]
                if len(payloads) != 1:
                    return blocked(
                        "transport_failure",
                        "Submission did not return a receipt; retry the same request.",
                    )
                result = json.loads(payloads[0])
                if not isinstance(result, dict) or not result.get("success"):
                    return (
                        result
                        if isinstance(result, dict)
                        else blocked("transport_failure", "Invalid submission receipt.")
                    )
                ref = result.get("agent_ref", "")
                if not ref.startswith(f"refs/evander/agents/{fresh.session_id}/"):
                    raise ValueError("Submission ref is outside the calling session")
                if binding.get("fixed_branch") and ref != private_branch_ref(
                    binding["fixed_branch"]
                ):
                    raise ValueError("Submission ref differs from its prepared branch")
                bare, _ = self.managed_git.resolve_internal_repository(
                    repo_id, fresh.directory_path
                )

                def read(*args, raw=False):
                    output = subprocess.run(
                        ["git", f"--git-dir={bare}", "-c", "core.hooksPath=/dev/null", *args],
                        check=True,
                        capture_output=True,
                        text=not raw,
                        timeout=30,
                    ).stdout
                    return output if raw else output.strip()

                if read("rev-parse", ref) != sha:
                    return blocked(
                        "concurrent_update", "The managed branch moved during verification."
                    )
                note_blob = read("notes", f"--ref={request['notes_ref']}", "list", sha)
                note = read("cat-file", "blob", note_blob, raw=True)
                if note != summary_markdown.encode("utf-8"):
                    return blocked(
                        "summary_conflict", "The managed note differs from the submitted summary."
                    )
                if read("rev-parse", ref) != sha:
                    return blocked(
                        "concurrent_update", "The managed branch moved during verification."
                    )
                receipt = dict(
                    success=True,
                    checkout_id=binding["checkout_id"],
                    managed_repo_id=repo_id,
                    commit_sha=sha,
                    agent_ref=ref,
                    agent_branch=logical_branch_name(ref),
                    notes_ref=request["notes_ref"],
                    changed=bool(result.get("changed")),
                )
                request_id = hashlib.sha256(
                    json.dumps(
                        [fresh.session_id, binding["checkout_id"], sha, summary_markdown],
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
                self.store.save_receipt(request_id, binding["checkout_id"], receipt)
                return receipt
            except (ValueError, OSError, subprocess.SubprocessError, RuntimeError):
                LOGGER.warning(
                    "Git submission could not be verified checkout=%s",
                    binding["checkout_id"],
                    exc_info=True,
                )
                return blocked(
                    "transport_failure", "Submission could not be verified; retry the same request."
                )
