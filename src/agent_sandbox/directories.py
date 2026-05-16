from __future__ import annotations

from collections.abc import Callable
from collections import defaultdict
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from agent_sandbox.repositories.directories import DirectoryRepository, JsonDirectoryRepository
from agent_sandbox.models import Directory, DirectoryDetails, DirectoryInfo, DirectoryPath

ROOT_DIRECTORY_ID = "root"
R = TypeVar("R")


class DirectoryStore:
    def __init__(self, path: Path, repository: DirectoryRepository | None = None) -> None:
        self._repository = repository or JsonDirectoryRepository(path)

    def ensure_root(self) -> None:
        items = self._repository.read_all()
        if not self._root_record_needs_repair(items):
            self._validate_tree(items)
            return
        self.update_all(lambda _items: None)

    def read_all(self) -> dict[str, Directory]:
        self.ensure_root()
        items = self._repository.read_all()
        self._validate_tree(items)
        return items

    def put(self, directory: Directory) -> None:
        if directory.id == ROOT_DIRECTORY_ID:
            if directory.name != ROOT_DIRECTORY_ID or directory.parent_id is not None:
                raise ValueError("root directory identity is immutable")

        def update(items: dict[str, Directory]) -> None:
            items[directory.id] = directory

        self.update_all(update)

    def delete(self, directory_id: str) -> None:
        if directory_id == ROOT_DIRECTORY_ID:
            raise ValueError("root directory cannot be deleted")

        def update(items: dict[str, Directory]) -> None:
            items.pop(directory_id, None)

        self.update_all(update)

    def get(self, directory_id: str) -> Directory | None:
        return self.read_all().get(directory_id)

    def write_all(self, items: dict[str, Directory]) -> None:
        self._validate_tree(items)
        self._repository.write_all(items)

    def update_all(self, updater: Callable[[dict[str, Directory]], R]) -> R:
        def update(items: dict[str, Directory]) -> R:
            self._ensure_root_record(items)
            result = updater(items)
            self._validate_tree(items)
            return result

        return self._repository.update_all(update)

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
                raise ValueError(
                    f"directory path segment {segment!r} does not exist under {cursor!r}"
                )
            cursor = next_directory.id
        return canonical

    @staticmethod
    def _root_record_needs_repair(items: dict[str, Directory]) -> bool:
        root = items.get(ROOT_DIRECTORY_ID)
        return (
            root is None
            or root.id != ROOT_DIRECTORY_ID
            or root.name != ROOT_DIRECTORY_ID
            or root.parent_id is not None
        )

    @staticmethod
    def _ensure_root_record(items: dict[str, Directory]) -> None:
        root = items.get(ROOT_DIRECTORY_ID)
        if root is None:
            items[ROOT_DIRECTORY_ID] = Directory(
                id=ROOT_DIRECTORY_ID, name=ROOT_DIRECTORY_ID, parent_id=None
            )
            return
        if (
            root.id != ROOT_DIRECTORY_ID
            or root.name != ROOT_DIRECTORY_ID
            or root.parent_id is not None
        ):
            items[ROOT_DIRECTORY_ID] = Directory(
                id=ROOT_DIRECTORY_ID,
                name=ROOT_DIRECTORY_ID,
                parent_id=None,
                created_at=root.created_at,
                metadata=root.metadata,
            )

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
                raise ValueError(
                    f"directory {directory.id!r} has unknown parent {directory.parent_id!r}"
                )
            sibling_key = (directory.parent_id, directory.name)
            if sibling_key in sibling_names:
                raise ValueError(
                    f"duplicate sibling directory name {directory.name!r} under parent {directory.parent_id!r}"
                )
            sibling_names.add(sibling_key)

        for directory_id in items:
            seen: set[str] = set()
            cursor = directory_id
            while cursor is not None:
                if cursor in seen:
                    raise ValueError(f"directory tree contains cycle at {cursor!r}")
                seen.add(cursor)
                cursor = items[cursor].parent_id


class DirectoryService:
    def __init__(self, store: DirectoryStore) -> None:
        self._store = store

    def create(self, *, parent: str, name: str) -> DirectoryInfo:
        def update(items: dict[str, Directory]) -> DirectoryInfo:
            parent_directory = self._resolve(parent, items)
            directory = Directory(id=uuid4().hex, name=name, parent_id=parent_directory.id)
            items[directory.id] = directory
            return self._to_info(directory, items)

        return self._store.update_all(update)

    def rename(self, reference: str, *, new_name: str) -> DirectoryInfo:
        def update(items: dict[str, Directory]) -> DirectoryInfo:
            directory = self._resolve(reference, items)
            self._ensure_not_root(directory.id, action="renamed")
            updated = directory.model_copy(update={"name": new_name})
            items[directory.id] = updated
            return self._to_info(updated, items)

        return self._store.update_all(update)

    def move(self, reference: str, *, new_parent: str) -> DirectoryInfo:
        def update(items: dict[str, Directory]) -> DirectoryInfo:
            directory = self._resolve(reference, items)
            self._ensure_not_root(directory.id, action="moved")
            new_parent_directory = self._resolve(new_parent, items)
            if new_parent_directory.id == directory.id:
                raise ValueError("directory cannot be moved into itself")
            descendant_ids = self._descendant_ids(directory.id, items)
            if new_parent_directory.id in descendant_ids:
                raise ValueError("directory cannot be moved into its descendant")
            updated = directory.model_copy(update={"parent_id": new_parent_directory.id})
            items[directory.id] = updated
            return self._to_info(updated, items)

        return self._store.update_all(update)

    def delete(self, reference: str, *, force: bool = False) -> dict[str, object]:
        def update(items: dict[str, Directory]) -> dict[str, object]:
            directory = self._resolve(reference, items)
            self._ensure_not_root(directory.id, action="deleted")
            descendant_ids = self._descendant_ids(directory.id, items)
            if descendant_ids and not force:
                raise ValueError("directory is not empty; pass force=True to delete recursively")
            deleted_ids = sorted({directory.id, *descendant_ids})
            for directory_id in deleted_ids:
                items.pop(directory_id, None)
            return {"deleted_count": len(deleted_ids), "deleted_ids": deleted_ids}

        return self._store.update_all(update)

    def list_children(self, *, parent: str = ROOT_DIRECTORY_ID) -> list[DirectoryInfo]:
        items = self._store.read_all()
        parent_directory = self._resolve(parent, items)
        children = self._children_by_parent(items).get(parent_directory.id, [])
        return [self._to_info(child, items) for child in children]

    def list_descendants(self, reference: str) -> list[DirectoryInfo]:
        items = self._store.read_all()
        directory = self._resolve(reference, items)
        descendants = sorted(
            (items[descendant_id] for descendant_id in self._descendant_ids(directory.id, items)),
            key=lambda candidate: (self._path_for_id(candidate.id, items), candidate.id),
        )
        return [self._to_info(candidate, items) for candidate in descendants]

    def show(self, reference: str) -> DirectoryDetails:
        items = self._store.read_all()
        directory = self._resolve(reference, items)
        ancestry = self._ancestry(directory.id, items)
        return DirectoryDetails(
            directory=self._to_info(directory, items),
            ancestry=[self._to_info(ancestor, items) for ancestor in ancestry],
        )

    def path_for(self, reference: str) -> str:
        items = self._store.read_all()
        directory = self._resolve(reference, items)
        return self._path_for_id(directory.id, items)

    def _resolve(self, reference: str, items: dict[str, Directory]) -> Directory:
        normalized = reference.strip()
        if not normalized:
            raise ValueError("directory reference cannot be empty")
        if normalized == ROOT_DIRECTORY_ID or "/" in normalized:
            canonical = DirectoryPath(segments=normalized).as_posix()
            cursor = items[ROOT_DIRECTORY_ID]
            for segment in canonical.split("/")[1:]:
                next_directory = None
                for candidate in self._children_by_parent(items).get(cursor.id, []):
                    if candidate.name == segment:
                        next_directory = candidate
                        break
                if next_directory is None:
                    raise ValueError(
                        f"directory path segment {segment!r} does not exist under {cursor.id!r}"
                    )
                cursor = next_directory
            return cursor
        directory = items.get(normalized)
        if directory is None:
            raise ValueError(f"directory {normalized!r} does not exist")
        return directory

    def _to_info(self, directory: Directory, items: dict[str, Directory]) -> DirectoryInfo:
        return DirectoryInfo.from_directory(directory, path=self._path_for_id(directory.id, items))

    def _path_for_id(self, directory_id: str, items: dict[str, Directory]) -> str:
        segments = [directory.name for directory in self._ancestry(directory_id, items)]
        return DirectoryPath(segments=segments).as_posix()

    def _ancestry(self, directory_id: str, items: dict[str, Directory]) -> list[Directory]:
        ancestry: list[Directory] = []
        cursor = items[directory_id]
        while True:
            ancestry.append(cursor)
            if cursor.parent_id is None:
                break
            cursor = items[cursor.parent_id]
        ancestry.reverse()
        return ancestry

    def _descendant_ids(self, directory_id: str, items: dict[str, Directory]) -> set[str]:
        children_by_parent = self._children_by_parent(items)
        stack = [directory_id]
        descendants: set[str] = set()
        while stack:
            parent_id = stack.pop()
            for child in children_by_parent.get(parent_id, []):
                if child.id in descendants:
                    continue
                descendants.add(child.id)
                stack.append(child.id)
        return descendants

    @staticmethod
    def _ensure_not_root(directory_id: str, *, action: str) -> None:
        if directory_id == ROOT_DIRECTORY_ID:
            raise ValueError(f"root directory cannot be {action}")

    @staticmethod
    def _children_by_parent(items: dict[str, Directory]) -> dict[str, list[Directory]]:
        children: dict[str, list[Directory]] = defaultdict(list)
        for directory in items.values():
            if directory.parent_id is not None:
                children[directory.parent_id].append(directory)
        for parent_id in children:
            children[parent_id].sort(key=lambda directory: (directory.name, directory.id))
        return dict(children)
