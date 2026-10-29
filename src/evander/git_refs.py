"""Separate upstream branches from Evander-owned logical branches.

Logical names such as ``agents/session/topic`` remain stable in workflow and
Team records. Their Git storage is private; an explicit ``refs/heads/...``
always names an upstream branch.
"""

from __future__ import annotations


PRIVATE_REF_PREFIX = "refs/evander/"
PRIVATE_BRANCH_ROOTS = frozenset({"agents", "review", "team"})


def private_branch_name(value: str) -> str:
    name = str(value).strip()
    for prefix in (PRIVATE_REF_PREFIX, "refs/heads/"):
        if name.startswith(prefix):
            name = name.removeprefix(prefix)
            break
    if name.split("/", 1)[0] not in PRIVATE_BRANCH_ROOTS or "/" not in name:
        raise ValueError("Expected an Evander agent, review, or Team branch")
    return name


def private_branch_ref(value: str) -> str:
    return PRIVATE_REF_PREFIX + private_branch_name(value)


def managed_branch_ref(value: str) -> str:
    """Resolve a logical branch, keeping explicit upstream refs unambiguous."""
    name = str(value).strip()
    if name.startswith("refs/"):
        return name
    if name.split("/", 1)[0] in PRIVATE_BRANCH_ROOTS and "/" in name:
        return private_branch_ref(name)
    return "refs/heads/" + name


def logical_branch_name(ref: str) -> str | None:
    for prefix in (PRIVATE_REF_PREFIX, "refs/heads/"):
        if ref.startswith(prefix):
            name = ref.removeprefix(prefix)
            if prefix == PRIVATE_REF_PREFIX and name.split("/", 1)[0] not in PRIVATE_BRANCH_ROOTS:
                return None
            return name or None
    return None
