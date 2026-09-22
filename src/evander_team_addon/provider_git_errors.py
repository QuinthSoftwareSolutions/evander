"""Provider git errors."""

from __future__ import annotations

from collections.abc import (
    Mapping,
    Sequence,
)


class DependencyGitPreparationError(RuntimeError):
    def __init__(
        self,
        *,
        task_id: str,
        message: str,
        conflict: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.task_id = task_id
        self.conflict = dict(conflict) if conflict is not None else None


class ProjectGitPreparationError(RuntimeError):
    def __init__(self, *, project_id: str, message: str) -> None:
        super().__init__(message)
        self.project_id = project_id


class ReviewRepositoryPreparationError(RuntimeError):
    """One or more review snapshots cannot be prepared safely."""

    def __init__(
        self,
        *,
        project_id: str,
        review_request_ids: Sequence[str],
        message: str,
    ) -> None:
        super().__init__(message)
        self.project_id = project_id
        self.review_request_ids = tuple(sorted(set(review_request_ids)))
