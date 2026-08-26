from __future__ import annotations

from collections.abc import Sequence

from .base import WorkflowTransform

BUILTIN_WORKFLOW_TRANSFORMS: dict[str, WorkflowTransform] = {}


def resolve_workflow_transforms(transform_names: Sequence[str]) -> list[WorkflowTransform]:
    unknown = sorted(name for name in transform_names if name not in BUILTIN_WORKFLOW_TRANSFORMS)
    if unknown:
        joined = ", ".join(unknown)
        raise ValueError(f"Unknown workflow transform(s): {joined}")
    return [BUILTIN_WORKFLOW_TRANSFORMS[name] for name in transform_names]
