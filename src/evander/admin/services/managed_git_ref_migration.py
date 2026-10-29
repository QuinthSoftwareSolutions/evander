"""Idempotent, compare-and-swap migration of host-owned Git refs."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import Any

from evander.admin.services.managed_git_common import ManagedGitConflictError
from evander.git_refs import PRIVATE_BRANCH_ROOTS


LAYOUT_MARKER = "refs/evander/layout/private-refs-v2"
LAYOUT_CONTENT = "evander private ref layout v2\n"
LAYOUT_LOCK = "evander-private-refs.lock"


def private_ref_layout_ready(run_git, bare_path: Path) -> bool:
    result = run_git(
        ["git", f"--git-dir={bare_path}", "rev-parse", "--verify", LAYOUT_MARKER],
        check=False,
    )
    if result.returncode:
        return False
    marker = run_git(
        ["git", f"--git-dir={bare_path}", "cat-file", "blob", LAYOUT_MARKER],
        check=False,
    )
    if marker.returncode or marker.stdout != LAYOUT_CONTENT:
        raise ManagedGitConflictError("Managed repository has an invalid private-ref layout marker")
    return True


def private_ref_migration_plan(run_git, bare_path: Path) -> dict[str, Any]:
    if private_ref_layout_ready(run_git, bare_path):
        return {"already_migrated": True, "moves": []}
    output = run_git(
        [
            "git",
            f"--git-dir={bare_path}",
            "for-each-ref",
            "--format=%(refname) %(objectname)",
            *[f"refs/heads/{root}" for root in sorted(PRIVATE_BRANCH_ROOTS)],
            "refs/evander",
        ],
    ).stdout
    refs = dict(line.split(" ", 1) for line in output.splitlines() if line)
    moves = []
    for old_ref, commit in sorted(refs.items()):
        if not old_ref.startswith("refs/heads/"):
            continue
        name = old_ref.removeprefix("refs/heads/")
        new_ref = "refs/evander/" + name
        existing = refs.get(new_ref)
        if existing is not None and existing != commit:
            raise ManagedGitConflictError(
                f"Private-ref migration would overwrite {new_ref}; preserve and reconcile both refs first",
                details={
                    "source_ref": old_ref,
                    "source_sha": commit,
                    "target_ref": new_ref,
                    "target_sha": existing,
                },
            )
        moves.append(
            {
                "source_ref": old_ref,
                "target_ref": new_ref,
                "commit_sha": commit,
                "target_exists": existing is not None,
            }
        )
    # Deleting a branch checked out by a host worktree would change its identity.
    worktrees = run_git(["git", f"--git-dir={bare_path}", "worktree", "list", "--porcelain"]).stdout
    active = {
        line.removeprefix("branch ")
        for line in worktrees.splitlines()
        if line.startswith("branch ")
    }
    if any(move["source_ref"] in active for move in moves):
        raise ManagedGitConflictError(
            "Detach host worktrees on legacy private branches before migrating refs"
        )
    return {"already_migrated": False, "moves": moves}


def migrate_private_refs(run_git, bare_path: Path) -> dict[str, Any]:
    # The SSH wrapper holds this same lock throughout receive-pack. Keep its
    # empty file readable by the restricted Git account; it contains no data.
    with (bare_path / LAYOUT_LOCK).open("a+") as lock:
        os.fchmod(lock.fileno(), 0o644)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            plan = private_ref_migration_plan(run_git, bare_path)
            if plan["already_migrated"]:
                return plan
            marker = run_git(
                ["git", f"--git-dir={bare_path}", "hash-object", "-w", "--stdin"],
                input_text=LAYOUT_CONTENT,
            ).stdout.strip()
            commands = ["start"]
            for move in plan["moves"]:
                action = "verify" if move["target_exists"] else "create"
                commands.append(f"{action} {move['target_ref']} {move['commit_sha']}")
                commands.append(f"delete {move['source_ref']} {move['commit_sha']}")
            commands.extend([f"create {LAYOUT_MARKER} {marker}", "prepare", "commit", ""])
            run_git(
                ["git", f"--git-dir={bare_path}", "update-ref", "--stdin"],
                input_text="\n".join(commands),
            )
            return plan
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
