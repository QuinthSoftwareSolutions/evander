"""Pin a checked workspace from inside Docker's host mount namespace."""

import argparse
import os
from pathlib import Path, PurePosixPath
import subprocess


def open_checked_workspace(root, relative, device, inode):
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("Expected a worker-relative workspace")
    descriptor = os.open(root, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts:
            next_descriptor = os.open(
                component, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = next_descriptor
        checked = os.fstat(descriptor)
        if (checked.st_dev, checked.st_ino) != (device, inode):
            raise ValueError("The checked workspace changed before pinning")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def pin_workspace(root, relative, target, device, inode):
    target = Path(target)
    if target.is_symlink() or not target.is_dir():
        raise ValueError("Expected a prepared private mountpoint")
    descriptor = open_checked_workspace(root, relative, device, inode)
    try:
        # The FD must be opened in this namespace: Linux rejects bind sources
        # referring to a mount owned by the supervisor's PrivateTmp namespace.
        source = f"/proc/{os.getpid()}/fd/{descriptor}"
        subprocess.run(
            ["mount", "--no-canonicalize", "--bind", source, str(target)],
            check=True, capture_output=True, timeout=30,
        )
        subprocess.run(
            ["mount", "--make-private", str(target)],
            check=True, capture_output=True, timeout=30,
        )
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-root", required=True)
    parser.add_argument("--relative", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--device", type=int, required=True)
    parser.add_argument("--inode", type=int, required=True)
    args = parser.parse_args()
    pin_workspace(args.worker_root, args.relative, args.target, args.device, args.inode)


if __name__ == "__main__":
    main()
