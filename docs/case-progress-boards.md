# Case progress boards

The Team task board can be filtered by case. Use the **Case** selector, or **View
task board** on a project case, then **Copy link** to share the view. The link opens
the correct Team and project after login. It grants no permissions: the existing
directory and Team access controls still apply. An unavailable case produces an
empty view rather than falling back to the whole project.

The case view includes directly linked tasks and all descendants through
`parent_task_id` or `originating_task_id`, including correction tasks that have a
specialized case. Dependency links alone do not confer case membership. Counts,
lanes, hierarchy and pagination operate on this scope; completed work is shown by
default. New tasks created from this board default to the selected case.

Task creation inherits case membership automatically. An explicit case wins;
otherwise the originating task's case takes precedence, followed by the parent's
case. This applies to Admin, workflow and agent commands, including batches where
children precede their parents. Inheritance stays within the project. Existing
tasks retain their case on an upsert unless an explicit case change is requested.

The `effect:team:create-case` action accepts `reuse_existing: true`. It requires
`source` and `source_ref` and atomically reuses the matching Team/project/source
case without changing its title, status, owner or task links. Conflicting IDs or
multiple existing matches fail. This option is appropriate for durable intake;
ordinary create-case behavior remains unchanged.
The create/update case outputs include a URL-encoded `task_board_path` for workflow
comments and other links, suitable for appending to the configured Admin base URL.

## Workflow configuration

Deploy version 0.38.0 before using these workflow fields. Deployment does not
modify workflow packs or post provider comments. Intake eligibility, case creation
and progress-link notifications belong in operator-owned workflows. For APP,
these changes are configured separately on the remote after deployment, using
the existing revision-checked workflow editing service.

## Existing APP case linkage

The optional standalone backfill helper previews existing case linkage. Run it
from the deployed checkout as the service account:

```bash
./.venv/bin/python scripts/backfill_app_intake_cases.py
```

The backfill uses stored ADO attribution and recognized ADO source references.
It reuses existing intake cases, normalizes their source identity, creates missing
cases, and links only tasks without a case. It preserves specialized cases and
all task states, posts no comments, and dispatches no work. Ambiguous matches are
reported and block application. Apply with `--apply` after reviewing the plan.
Concurrent Team edits raise a revision conflict; rerun the preview to continue
from the already-applied portion.

The helper uses supported revision-checked Team services, never direct state or
workflow-pack edits. Existing work can use the board after backfill; sharing links
for existing items is a separate publication step.

The case board reports task execution and completion; it does not treat an intake
task being finished as proof that the whole case has been released or deployed.
