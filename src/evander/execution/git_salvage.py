"""Capture reviewable Git evidence without changing the original index or checkout.

This standard-library-only function is also transported as a host-owned setup
script to libvirt guests. Project code itself travels through scoped Git refs.
"""


def capture_git_salvage(
    *, repository, worktree, expected_branch, recovery_branch, destination="origin", env=None
):
    import os
    import subprocess
    import tempfile
    import stat
    from pathlib import Path

    repo = Path(repository).resolve(strict=True)
    target = Path(worktree).resolve(strict=True)
    environment = dict(os.environ if env is None else env)
    for key in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        environment.pop(key, None)
    environment.update(
        GIT_AUTHOR_NAME="Git recovery",
        GIT_AUTHOR_EMAIL="recovery@evander.invalid",
        GIT_COMMITTER_NAME="Git recovery",
        GIT_COMMITTER_EMAIL="recovery@evander.invalid",
        GIT_AUTHOR_DATE="2000-01-01T00:00:00Z",
        GIT_COMMITTER_DATE="2000-01-01T00:00:00Z",
    )

    def git(*args, directory=target, extra_env=None, data=None, inventory=False):
        output = subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "protocol.ext.allow=never",
                "-C",
                str(directory),
                *args,
            ],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True,
            errors="surrogateescape",
            env={**environment, **(extra_env or {})},
            timeout=60,
        ).stdout
        return output if inventory else output.strip()

    git("check-ref-format", "--branch", recovery_branch)
    if not recovery_branch.startswith("agents/"):
        raise ValueError("salvage must use a scoped agent branch")
    common_dir = git("rev-parse", "--path-format=absolute", "--git-common-dir")
    if (
        common_dir != git("rev-parse", "--path-format=absolute", "--git-common-dir", directory=repo)
        or Path(common_dir).resolve() != repo / ".git"
    ):
        raise ValueError("retained worktree belongs to a different repository")
    if git("branch", "--show-current") != expected_branch:
        raise ValueError("retained worktree branch changed; preserve it for a new diagnosis")
    head = git("rev-parse", "HEAD")
    # A conflicted index cannot be silently flattened into approved content.
    if git("ls-files", "--unmerged"):
        raise ValueError(
            "retained index contains unresolved conflicts; use reviewed merge recovery"
        )
    index_tree = git("write-tree")

    def read_entry(relative):
        # Walk directory descriptors without following symlinks, including during
        # concurrent guest writes. Never read arbitrary host paths as worker root.
        parts = Path(relative).parts
        if (
            not parts
            or Path(relative).is_absolute()
            or any(part in {"..", ".git"} for part in parts)
        ):
            raise ValueError("salvage path escapes the retained checkout")
        parent = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.close(parent)
                parent = child
            info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                return info, os.fsencode(os.readlink(parts[-1], dir_fd=parent))
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(
                    "salvage encountered a submodule or non-regular file; retain it for scoped recovery"
                )
            descriptor = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
            with os.fdopen(descriptor, "rb") as stream:
                actual = os.fstat(stream.fileno())
                if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (
                    info.st_dev,
                    info.st_ino,
                ):
                    raise ValueError("retained file changed during salvage")
                data = stream.read(512 * 1024 * 1024 + 1)
                if len(data) > 512 * 1024 * 1024:
                    raise ValueError(
                        "salvage exceeds the byte limit; original files remain retained"
                    )
                return actual, data
        finally:
            os.close(parent)

    def snapshot(index):
        temporary_env = {"GIT_INDEX_FILE": index}
        git("read-tree", "--empty", extra_env=temporary_env)
        paths = sorted(
            set(
                git(
                    "ls-files", "--cached", "--others", "--exclude-standard", "-z", inventory=True
                ).split("\0")
            )
            - {""}
        )
        if len(paths) > 20000:
            raise ValueError(
                "salvage exceeds the file inventory limit; retain the workspace for scoped salvage"
            )
        entries = []
        size = 0
        for relative in paths:
            try:
                info, data = read_entry(relative)
            except FileNotFoundError:
                continue
            size += len(data)
            if size > 512 * 1024 * 1024:
                raise ValueError("salvage exceeds the byte limit; original files remain retained")
            # No guest-defined clean filters, fsmonitor, or hooks run as worker root.
            oid = (
                subprocess.run(
                    ["git", "-C", str(target), "hash-object", "-w", "--no-filters", "--stdin"],
                    input=data,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True,
                    env=environment,
                    timeout=60,
                )
                .stdout.decode()
                .strip()
            )
            mode = (
                "120000"
                if stat.S_ISLNK(info.st_mode)
                else "100755"
                if info.st_mode & stat.S_IXUSR
                else "100644"
            )
            entries.append(f"{mode} {oid}\t{relative}\0")
        git("update-index", "-z", "--index-info", extra_env=temporary_env, data="".join(entries))
        return git("write-tree", extra_env=temporary_env)

    with tempfile.TemporaryDirectory(prefix="evander-salvage-") as temporary:
        index = str(Path(temporary) / "index")
        tree = snapshot(index)
        # Detect concurrent writes before publishing evidence. The original data
        # remains retained even after this stable snapshot has been made.
        if (
            tree != snapshot(index)
            or head != git("rev-parse", "HEAD")
            or index_tree != git("write-tree")
        ):
            raise ValueError("retained work changed while capturing salvage; retry a fresh preview")
    index_commit = git(
        "commit-tree", index_tree, "-p", head, data="Retained index for reviewed Git recovery\n"
    )
    commit = git(
        "commit-tree", tree, "-p", head, data="Retained working content for reviewed Git recovery\n"
    )
    # Never overwrite evidence. Repeating the same capture generates identical
    # commits; changed content gets different immutable ref names.
    work_ref = f"refs/heads/{recovery_branch}/work-{commit}"
    index_ref = f"refs/heads/{recovery_branch}/index-{index_commit}"
    git("push", "--atomic", destination, f"{commit}:{work_ref}", f"{index_commit}:{index_ref}")
    ignored = git("ls-files", "--others", "--ignored", "--exclude-standard", "-z", inventory=True)
    return {
        "commit_sha": commit,
        "tree_hash": tree,
        "observed_head": head,
        "index_commit_sha": index_commit,
        "work_ref": work_ref,
        "index_ref": index_ref,
        "ignored_file_count": len([path for path in ignored.split("\0") if path]),
        "original_worktree_retained": True,
    }
