"""Deterministic Git projection for reviewing link objects through path-only SDKs.

Only assigned symlinks become regular metadata files. All other tree entries
are unchanged. The host independently computes the expected commit, without
writing objects, before accepting a native receipt for this derived snapshot.
"""

import base64
import hashlib
import json
import os
import re
import subprocess


def git_bytes(repo, *args, data=None):
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    environment.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_NO_REPLACE_OBJECTS="1",
        GIT_TERMINAL_PROMPT="0",
    )
    return subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(repo),
            *args,
        ],
        input=data,
        capture_output=True,
        check=True,
        timeout=120,
        env=environment,
    ).stdout


def tree_entries(repo, revision):
    if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", revision):
        raise ValueError("Invalid projection source revision")
    entries = []
    for record in git_bytes(repo, "ls-tree", "-r", "-z", "-l", "--full-tree", revision).split(
        b"\0"
    ):
        if not record:
            continue
        metadata, path = record.split(b"\t", 1)
        mode, _, oid, size = metadata.split()
        entries.append(
            {
                "path": path.decode("utf-8", "surrogateescape"),
                "mode": mode.decode(),
                "oid": oid.decode(),
                "size": 0 if size == b"-" else int(size),
            }
        )
    return entries


def symlink_projection(repo, revision, paths, *, materialize=False):
    """Return a source-bound projection identity, or None for ordinary scopes.

    Read link payloads from Git blobs, never through filesystem links. The same
    pure Git-object construction runs on the host and in the isolated checkout;
    only the latter writes the derived objects and checks out the result.
    """
    entries = tree_entries(repo, revision)
    selected = {x["path"]: x for x in entries if x["mode"] == "120000" and x["path"] in paths}
    if not selected:
        return None
    algorithm = "sha1" if len(revision) == 40 else "sha256"

    def object_id(kind, body, *, write=False):
        oid = hashlib.new(
            algorithm, kind.encode() + b" " + str(len(body)).encode() + b"\0" + body
        ).hexdigest()
        if write and materialize:
            actual = (
                git_bytes(repo, "hash-object", "-w", "-t", kind, "--stdin", data=body)
                .decode()
                .strip()
            )
            if actual != oid:
                raise ValueError("Projection Git object identity mismatch")
        return oid

    replacements, descriptors = {}, []
    for path, entry in sorted(selected.items()):
        # Linux cannot materialize link targets this large. Keep such an entry
        # blocked instead of allocating an unbounded metadata review payload.
        if entry["size"] > 1024 * 1024:
            raise ValueError("Symlink payload is too large for a metadata review")
        target = git_bytes(repo, "cat-file", "blob", entry["oid"])
        if len(target) != entry["size"] or object_id("blob", target) != entry["oid"]:
            raise ValueError("Projection source link failed verification")
        descriptor = {
            "document_type": "evander.symlink-review",
            "version": 1,
            "source_revision": revision,
            "path": path,
            "mode": entry["mode"],
            "object_id": entry["oid"],
            "target_base64": base64.b64encode(target).decode(),
            "target_text": target.decode("utf-8", "backslashreplace"),
        }
        body = (json.dumps(descriptor, sort_keys=True, ensure_ascii=True, indent=2) + "\n").encode()
        oid = object_id("blob", body, write=True)
        replacements[path] = oid
        descriptors.append(
            {**descriptor, "projection_object_id": oid, "sha256": hashlib.sha256(body).hexdigest()}
        )

    changes = {}
    for path, oid in replacements.items():
        parts = path.encode("utf-8", "surrogateescape").split(b"/")
        parent = changes
        for part in parts[:-1]:
            parent = parent.setdefault(part, {})
        parent[parts[-1]] = oid

    def build_tree(oid, node):
        original = git_bytes(repo, "cat-file", "tree", oid)
        if object_id("tree", original) != oid:
            raise ValueError("Projection source tree failed verification")
        records, offset = [], 0
        while offset < len(original):
            separator = original.index(b" ", offset)
            terminator = original.index(b"\0", separator)
            mode, name = original[offset:separator], original[separator + 1 : terminator]
            end = terminator + 1 + len(revision) // 2
            child_oid = original[terminator + 1 : end].hex()
            change = node.get(name)
            if change is None:
                # Preserve unassigned entries, including empty trees and gitlinks,
                # byte for byte. Read only ancestors of the assigned links.
                records.append(original[offset:end])
            else:
                if isinstance(change, dict):
                    if mode != b"40000":
                        raise ValueError("Projection path parent is not a Git tree")
                    child_oid = build_tree(child_oid, change)
                else:
                    if mode != b"120000":
                        raise ValueError("Projection source entry is not a symlink")
                    mode, child_oid = b"100644", change
                records.append(mode + b" " + name + b"\0" + bytes.fromhex(child_oid))
            offset = end
        return object_id("tree", b"".join(records), write=True)

    source_tree = git_bytes(repo, "rev-parse", revision + "^{tree}").decode().strip()
    tree_oid = build_tree(source_tree, changes)
    commit = (
        f"tree {tree_oid}\nparent {revision}\n"
        "author Evander coverage <coverage@evander.invalid> 0 +0000\n"
        "committer Evander coverage <coverage@evander.invalid> 0 +0000\n\n"
        f"Evander symlink review projection v1\nSource: {revision}\n"
    ).encode()
    projected_revision = object_id("commit", commit, write=True)
    return {
        "version": 1,
        "source_revision": revision,
        "revision": projected_revision,
        "paths": sorted(selected),
        "descriptors": descriptors,
    }
