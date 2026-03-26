from __future__ import annotations

from pathlib import Path

from .models import Directory, DirectoryPath
from .storage import JsonStore

ROOT_DIRECTORY_ID = "root"


class DirectoryStore:
    def __init__(self, path: Path) -> None:
        self._store = JsonStore(path, Directory)

    def ensure_root(self) -> None:
        items = self._store.read_all()
        root = items.get(ROOT_DIRECTORY_ID)
        if root is None:
            items[ROOT_DIRECTORY_ID] = Directory(id=ROOT_DIRECTORY_ID, name=ROOT_DIRECTORY_ID, parent_id=None)
            self._store.write_all(items)
            return
        if root.id != ROOT_DIRECTORY_ID or root.name != ROOT_DIRECTORY_ID or root.parent_id is not None:
            items[ROOT_DIRECTORY_ID] = Directory(
                id=ROOT_DIRECTORY_ID,
                name=ROOT_DIRECTORY_ID,
                parent_id=None,
                created_at=root.created_at,
                metadata=root.metadata,
            )
            self._store.write_all(items)

    def read_all(self) -> dict[str, Directory]:
        self.ensure_root()
        items = self._store.read_all()
        self._validate_tree(items)
        return items

    def put(self, directory: Directory) -> None:
        items = self._store.read_all()
        if directory.id == ROOT_DIRECTORY_ID:
            if directory.name != ROOT_DIRECTORY_ID or directory.parent_id is not None:
                raise ValueError("root directory identity is immutable")
        items[directory.id] = directory
        self._validate_tree(items)
        self._store.write_all(items)

    def delete(self, directory_id: str) -> None:
        if directory_id == ROOT_DIRECTORY_ID:
            raise ValueError("root directory cannot be deleted")
        items = self._store.read_all()
        items.pop(directory_id, None)
        self._validate_tree(items)
        self._store.write_all(items)

    def get(self, directory_id: str) -> Directory | None:
        return self.read_all().get(directory_id)

    def validate_path(self, path: str) -> str:
        canonical = DirectoryPath(segments=path).as_posix()
        items = self.read_all()
        cursor = ROOT_DIRECTORY_ID
        for segment in canonical.split("/")[1:]:
            next_directory = None
            for candidate in items.values():
                if candidate.parent_id == cursor and candidate.name == segment:
                    next_directory = candidate
                    break
            if next_directory is None:
                raise ValueError(f"directory path segment {segment!r} does not exist under {cursor!r}")
            cursor = next_directory.id
        return canonical

    @staticmethod
    def _validate_tree(items: dict[str, Directory]) -> None:
        root = items.get(ROOT_DIRECTORY_ID)
        if root is None:
            raise ValueError("root directory must exist")
        if root.name != ROOT_DIRECTORY_ID or root.parent_id is not None:
            raise ValueError("root directory identity is immutable")

        sibling_names: set[tuple[str, str]] = set()
        for directory in items.values():
            if directory.id == ROOT_DIRECTORY_ID:
                continue
            if not directory.parent_id:
                raise ValueError(f"directory {directory.id!r} must specify parent_id")
            if directory.parent_id not in items:
                raise ValueError(f"directory {directory.id!r} has unknown parent {directory.parent_id!r}")
            sibling_key = (directory.parent_id, directory.name)
            if sibling_key in sibling_names:
                raise ValueError(
                    f"duplicate sibling directory name {directory.name!r} under parent {directory.parent_id!r}"
                )
            sibling_names.add(sibling_key)
