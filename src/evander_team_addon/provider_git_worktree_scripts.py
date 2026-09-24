"""Build the libvirt task worktree command without executing it."""

from __future__ import annotations

import json
import shlex
from collections.abc import (
    Mapping,
    Sequence,
)
from typing import (
    Any,
)

from evander.git_identity import (
    TEAM_DEPENDENCY_COMPOSER_GIT_IDENTITY,
    GitIdentity,
)

from .git_naming import (
    validate_branch_name,
)
from .provider_values import (
    _optional_text,
    _required_text,
)


def _build_vm_task_worktree_command(
    *,
    agent_branch: str,
    base_ref: str,
    dependency_composition_mode: str,
    dependency_composition_recovery_id: str | None,
    dependency_git_sources: Sequence[Mapping[str, str]],
    dependency_merge_author_identity: GitIdentity,
    pinned_base_commit: str | None,
    pinned_base_tree: str | None,
    prior_base_commit: str | None,
    project_repo: Mapping[str, Any],
    repo_name: str,
    resolved_dependency_branch: str | None,
    resolved_dependency_commit_sha: str | None,
    resolved_worktree_name: str,
    task_author_identity: GitIdentity | None,
    task_id: str,
) -> tuple[str, str, str]:
    clone_dir = _required_text(project_repo.get("target_dir"), "target_dir")
    worktree_path = f"/home/agent/worktrees/{repo_name}/{resolved_worktree_name}"
    quoted_target = shlex.quote(worktree_path)
    quoted_branch = shlex.quote(agent_branch)
    quoted_base = shlex.quote(base_ref)
    quoted_prior_base = shlex.quote(prior_base_commit or "")
    quoted_pinned_base = shlex.quote(pinned_base_commit or "")
    quoted_pinned_tree = shlex.quote(pinned_base_tree or "")
    worktree_dependency_provenance = [
        {
            "dependency_task_id": _required_text(
                row.get("dependency_task_id"),
                "dependency_task_id",
            ),
            "deliverable_id": _required_text(row.get("deliverable_id"), "deliverable_id"),
            "branch": _required_text(row.get("branch"), "dependency branch"),
            "commit_sha": _required_text(
                row.get("commit_sha"),
                "dependency commit_sha",
            ).casefold(),
            **(
                {"relationship_kind": row.get("relationship_kind")}
                if row.get("relationship_kind") not in {None, "task_dependency"}
                else {}
            ),
            **(
                {"pinned_ref": row.get("pinned_ref")}
                if _optional_text(row.get("pinned_ref")) is not None
                else {}
            ),
        }
        for row in dependency_git_sources
    ]
    dependency_provenance_json = json.dumps(
        worktree_dependency_provenance,
        sort_keys=True,
        separators=(",", ":"),
    )
    quoted_dependency_provenance = shlex.quote(dependency_provenance_json)
    if dependency_composition_mode not in {
        "automatic",
        "manual_recovery",
        "adopt_resolved",
    }:
        raise ValueError(f"unsupported dependency composition mode {dependency_composition_mode!r}")
    recovery_payload_json = json.dumps(
        {
            "recovery_id": dependency_composition_recovery_id,
            "dependency_git_sources": worktree_dependency_provenance,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    quoted_dependency_composition_mode = shlex.quote(dependency_composition_mode)
    quoted_dependency_recovery_id = shlex.quote(dependency_composition_recovery_id or "")
    quoted_manual_recovery_payload = shlex.quote(recovery_payload_json)
    quoted_resolved_dependency_branch = shlex.quote(
        validate_branch_name(resolved_dependency_branch)
        if resolved_dependency_branch is not None
        else ""
    )
    quoted_resolved_dependency_commit = shlex.quote(
        resolved_dependency_commit_sha.casefold()
        if resolved_dependency_commit_sha is not None
        else ""
    )
    quoted_dependency_author_name = shlex.quote(dependency_merge_author_identity.name)
    quoted_dependency_author_email = shlex.quote(dependency_merge_author_identity.email)
    quoted_dependency_committer_name = shlex.quote(TEAM_DEPENDENCY_COMPOSER_GIT_IDENTITY.name)
    quoted_dependency_committer_email = shlex.quote(TEAM_DEPENDENCY_COMPOSER_GIT_IDENTITY.email)
    quoted_dependency_commit_message = shlex.quote(
        f"Compose accepted dependencies for task {task_id}"
    )
    lock_path = f"/home/agent/.evander/locks/task-worktree-{repo_name}.lock"
    quoted_lock = shlex.quote(lock_path)
    dependency_assignment_lines: list[str] = []
    dependency_fetch_lines: list[str] = []
    dependency_frontier_lines: list[str] = []
    dependency_merge_lines: list[str] = []
    dependency_parent_lines: list[str] = []
    dependency_verify_lines: list[str] = []
    dependency_adoption_lines: list[str] = []
    dependency_timestamp_lines: list[str] = []
    for index, source in enumerate(dependency_git_sources):
        source_branch = shlex.quote(
            validate_branch_name(_required_text(source.get("branch"), "dependency branch"))
        )
        source_commit = shlex.quote(
            _required_text(source.get("commit_sha"), "dependency commit_sha").casefold()
        )
        pinned_ref = _optional_text(source.get("pinned_ref"))
        source_ref = shlex.quote(
            pinned_ref
            or f"refs/heads/{validate_branch_name(_required_text(source.get('branch'), 'dependency branch'))}"
        )
        source_task = shlex.quote(
            _required_text(source.get("dependency_task_id"), "dependency_task_id")
        )
        source_deliverable = shlex.quote(
            _required_text(source.get("deliverable_id"), "deliverable_id")
        )
        dependency_assignment_lines.extend(
            (
                f"dependency_branch_{index}={source_branch}",
                f"dependency_commit_{index}={source_commit}",
                f"dependency_ref_{index}={source_ref}",
            )
        )
        dependency_fetch_lines.extend(
            (
                (f'git fetch --no-tags origin "$dependency_ref_{index}"'),
                (
                    f'test "$(git rev-parse --verify FETCH_HEAD^{{commit}})" '
                    f'= "$dependency_commit_{index}"'
                ),
            )
        )
        dependency_merge_lines.extend(
            (
                f'if [ "$dependency_needed_{index}" = 1 ]; then',
                (
                    f'  if ! git -C "$target" merge --no-ff -m '
                    f'"Temporary Team dependency integration" '
                    f'"$dependency_commit_{index}"; then'
                ),
                '    conflict_paths="$(git -C "$target" diff --name-only --diff-filter=U)"',
                '    if [ -n "$conflict_paths" ]; then',
                '      conflict_base_tree="$(git -C "$target" rev-parse "$head_before^{tree}")"',
                '      echo "EVANDER_DEPENDENCY_CONFLICT=1" >&2',
                '      echo "EVANDER_DEPENDENCY_CONFLICT_BASE_COMMIT=$head_before" >&2',
                '      echo "EVANDER_DEPENDENCY_CONFLICT_BASE_TREE=$conflict_base_tree" >&2',
                f"      echo EVANDER_DEPENDENCY_CONFLICT_FAILED_TASK={source_task} >&2",
                f"      echo EVANDER_DEPENDENCY_CONFLICT_FAILED_DELIVERABLE={source_deliverable} >&2",
                (
                    '      printf "%s\\n" "$conflict_paths" '
                    "| sed 's/^/EVANDER_DEPENDENCY_CONFLICT_PATH=/' >&2"
                ),
                "    fi",
                '    git -C "$target" merge --abort >/dev/null 2>&1 || true',
                '    git -C "$target" reset --hard "$head_before" >/dev/null',
                (
                    f"    echo dependency Git merge failed for task "
                    f"{source_task} deliverable {source_deliverable} >&2"
                ),
                "    exit 1",
                "  fi",
                "fi",
            )
        )
        dependency_frontier_lines.extend(
            (
                f"dependency_needed_{index}=1",
                (
                    f'if git -C "$target" merge-base --is-ancestor '
                    f'"$dependency_commit_{index}" "$head_before"; then '
                    f"dependency_needed_{index}=0; fi"
                ),
            )
        )
        dependency_parent_lines.extend(
            (
                f'if [ "$dependency_needed_{index}" = 1 ]; then',
                f'  set -- "$@" -p "$dependency_commit_{index}"',
                "fi",
            )
        )
        dependency_verify_lines.append(
            (f'git -C "$target" merge-base --is-ancestor "$dependency_commit_{index}" HEAD')
        )
        dependency_adoption_lines.append(
            (
                f'if ! git -C "$target" merge-base --is-ancestor '
                f'"$dependency_commit_{index}" "$head_before"; then '
                "all_dependencies_present=0; fi"
            )
        )
        dependency_timestamp_lines.extend(
            (
                f'if [ "$dependency_needed_{index}" = 1 ]; then',
                (
                    f'  parent_timestamp="$(git -C "$target" show -s '
                    f'--format=%ct "$dependency_commit_{index}")"'
                ),
                (
                    '  if [ "$parent_timestamp" -gt "$max_parent_timestamp" ]; '
                    'then max_parent_timestamp="$parent_timestamp"; fi'
                ),
                "fi",
            )
        )
    for index, _source in enumerate(dependency_git_sources):
        for other_index, _other_source in enumerate(dependency_git_sources):
            if index == other_index:
                continue
            if other_index < index:
                dependency_frontier_lines.extend(
                    (
                        f'if [ "$dependency_needed_{index}" = 1 ] '
                        f'&& [ "$dependency_commit_{index}" = '
                        f'"$dependency_commit_{other_index}" ]; then',
                        f"  dependency_needed_{index}=0",
                        "fi",
                    )
                )
            dependency_frontier_lines.extend(
                (
                    f'if [ "$dependency_needed_{index}" = 1 ] '
                    f'&& [ "$dependency_commit_{index}" != '
                    f'"$dependency_commit_{other_index}" ] '
                    f'&& git -C "$target" merge-base --is-ancestor '
                    f'"$dependency_commit_{index}" "$dependency_commit_{other_index}"; then',
                    f"  dependency_needed_{index}=0",
                    "fi",
                )
            )
    dependency_frontier_lines.extend(("frontier_count=0", 'frontier_commit=""'))
    for index, _source in enumerate(dependency_git_sources):
        dependency_frontier_lines.extend(
            (
                f'if [ "$dependency_needed_{index}" = 1 ]; then',
                "  frontier_count=$((frontier_count + 1))",
                f'  frontier_commit="$dependency_commit_{index}"',
                "fi",
            )
        )
    command_lines = [
        "set -eu",
        f"export GIT_AUTHOR_NAME={quoted_dependency_author_name}",
        f"export GIT_AUTHOR_EMAIL={quoted_dependency_author_email}",
        f"export GIT_COMMITTER_NAME={quoted_dependency_committer_name}",
        f"export GIT_COMMITTER_EMAIL={quoted_dependency_committer_email}",
        "export GIT_MERGE_AUTOEDIT=no",
        f"target={quoted_target}",
        f"branch={quoted_branch}",
        f"base={quoted_base}",
        'base_commitish="$base"',
        f"expected_prior_base={quoted_prior_base}",
        f"pinned_base_commit={quoted_pinned_base}",
        f"pinned_base_tree={quoted_pinned_tree}",
        f"expected_dependency_provenance={quoted_dependency_provenance}",
        f"dependency_composition_mode={quoted_dependency_composition_mode}",
        f"dependency_recovery_id={quoted_dependency_recovery_id}",
        f"manual_recovery_payload={quoted_manual_recovery_payload}",
        f"resolved_dependency_branch={quoted_resolved_dependency_branch}",
        f"resolved_dependency_commit={quoted_resolved_dependency_commit}",
        f"dependency_commit_message={quoted_dependency_commit_message}",
        f"lock={quoted_lock}",
        'mkdir -p "$(dirname "$lock")"',
        'exec 9>"$lock"',
        "flock 9",
        'mkdir -p "$(dirname "$target")"',
        "git config extensions.worktreeConfig true",
        'if [ -n "$pinned_base_commit" ]; then',
        '  if [ "$base" = "$pinned_base_commit" ]; then',
        ('    test "$(git rev-parse --verify "$base^{commit}")" = "$pinned_base_commit"'),
        "  else",
        '    git fetch --no-tags origin "refs/heads/$base"',
        '    if [ -n "$pinned_base_tree" ]; then',
        ('      test "$(git rev-parse --verify FETCH_HEAD^{commit})" = "$pinned_base_commit"'),
        "    else",
        (
            '      git merge-base --is-ancestor "$pinned_base_commit" '
            '"$(git rev-parse --verify FETCH_HEAD^{commit})"'
        ),
        "    fi",
        "  fi",
        '  if [ -n "$pinned_base_tree" ]; then',
        ('    test "$(git rev-parse --verify "$pinned_base_commit^{tree}")" = "$pinned_base_tree"'),
        "  fi",
        '  base_commitish="$pinned_base_commit"',
        "fi",
        "created=0",
        'if [ -e "$target/.git" ]; then',
        '  test "$(git -C "$target" branch --show-current)" = "$branch"',
        'elif git show-ref --verify --quiet "refs/heads/$branch"; then',
        '  git worktree add "$target" "$branch"',
        "else",
        '  if [ -z "$pinned_base_commit" ]; then',
        '    if [ "$base" = "HEAD" ]; then',
        "      git fetch --no-tags origin HEAD",
        "    else",
        '      git fetch --no-tags origin "refs/heads/$base"',
        "    fi",
        '    base_commitish="$(git rev-parse --verify FETCH_HEAD^{commit})"',
        "  fi",
        '  git worktree add -b "$branch" "$target" "$base_commitish"',
        "  created=1",
        "fi",
        (
            'if [ "$created" != 1 ] && [ -z "$expected_prior_base" ] '
            '&& [ -z "$pinned_base_commit" ]; then'
        ),
        (
            '  echo "reused task worktree has no authoritative server-recorded base; '
            'use an audited provenance recovery" >&2'
        ),
        "  exit 1",
        "fi",
        'git -C "$target" config --worktree evander.workBranch "$branch"',
        'git -C "$target" config --worktree evander.baseBranch "$base"',
        (
            'if [ "$created" = 1 ]; then git -C "$target" config --worktree '
            'evander.baseCommit "$(git -C "$target" rev-parse HEAD)"; fi'
        ),
        (
            'configured_base="$(git -C "$target" config --worktree --get '
            'evander.baseCommit || true)"'
        ),
        (
            'configured_composition_root="$(git -C "$target" config --worktree --get '
            'evander.dependencyCompositionRootCommit || true)"'
        ),
        (
            'configured_composed_commit="$(git -C "$target" config --worktree --get '
            'evander.dependencyComposedCommit || true)"'
        ),
        'head_before="$(git -C "$target" rev-parse HEAD)"',
        'if [ -n "$pinned_base_commit" ]; then',
        ('  git -C "$target" merge-base --is-ancestor "$pinned_base_commit" "$head_before"'),
        "fi",
        'if [ -n "$expected_prior_base" ]; then',
        (
            '  if [ -n "$configured_base" ]; then test "$configured_base" = '
            '"$expected_prior_base"; else'
        ),
        ('    git -C "$target" merge-base --is-ancestor "$expected_prior_base" "$head_before"'),
        '    configured_base="$expected_prior_base"',
        "  fi",
        "fi",
        *dependency_assignment_lines,
        'if [ "$dependency_composition_mode" = "manual_recovery" ]; then',
        (
            '  current_manual_recovery="$(git -C "$target" config --worktree '
            '--get evander.manualDependencyGitSources || true)"'
        ),
        '  if [ -n "$current_manual_recovery" ]; then',
        '    test "$current_manual_recovery" = "$manual_recovery_payload"',
        "  else",
        '    test -z "$(git -C "$target" status --porcelain)"',
        (
            '    if [ "$created" != 1 ]; then test -n "$configured_base"; '
            'test "$head_before" = "$configured_base"; fi'
        ),
        *[f"    {line}" for line in dependency_fetch_lines],
        (
            '    git -C "$target" config --worktree '
            'evander.manualDependencyGitSources "$manual_recovery_payload"'
        ),
        "  fi",
        'elif [ "$dependency_composition_mode" = "adopt_resolved" ]; then',
        '  test -n "$dependency_recovery_id"',
        '  test -n "$resolved_dependency_branch"',
        '  test -n "$resolved_dependency_commit"',
        '  git fetch --no-tags origin "refs/heads/$resolved_dependency_branch"',
        ('  test "$(git rev-parse --verify FETCH_HEAD^{commit})" = "$resolved_dependency_commit"'),
        *[f"  {line}" for line in dependency_fetch_lines],
        (
            '  git -C "$target" merge-base --is-ancestor '
            '"$pinned_base_commit" "$resolved_dependency_commit"'
        ),
        *[
            (
                f'  git -C "$target" merge-base --is-ancestor '
                f'"$dependency_commit_{index}" "$resolved_dependency_commit"'
            )
            for index, _source in enumerate(dependency_git_sources)
        ],
        '  test -z "$(git -C "$target" status --porcelain)"',
        (
            '  test "$head_before" = "$pinned_base_commit" '
            '|| test "$head_before" = "$resolved_dependency_commit"'
        ),
        '  git -C "$target" reset --hard "$resolved_dependency_commit" >/dev/null',
        (
            '  git -C "$target" config --worktree '
            'evander.dependencyGitProvenance "$expected_dependency_provenance"'
        ),
        (
            '  git -C "$target" config --worktree '
            'evander.dependencyCompositionRecovery "$dependency_recovery_id"'
        ),
        ('  git -C "$target" config --worktree evander.baseCommit "$resolved_dependency_commit"'),
        '  configured_base="$resolved_dependency_commit"',
        "else",
        (
            'current_dependency_provenance="$(git -C "$target" config '
            '--worktree --get evander.dependencyGitProvenance || true)"'
        ),
        'if [ -n "$current_dependency_provenance" ]; then',
        ('  test "$current_dependency_provenance" = "$expected_dependency_provenance"'),
        *[f"  {line}" for line in dependency_verify_lines],
        'elif [ "$expected_dependency_provenance" != "[]" ]; then',
        '  test -z "$(git -C "$target" status --porcelain)"',
        *[f"  {line}" for line in dependency_fetch_lines],
        "  all_dependencies_present=1",
        *[f"  {line}" for line in dependency_adoption_lines],
        '  if [ "$all_dependencies_present" = 1 ]; then',
        '    test -n "$configured_base"',
        (
            '    git -C "$target" config --worktree '
            'evander.dependencyGitProvenance "$expected_dependency_provenance"'
        ),
        ('    git -C "$target" config --worktree evander.baseCommit "$configured_base"'),
        "  else",
        (
            '    if [ "$created" != 1 ]; then test -n "$configured_base"; '
            'test "$head_before" = "$configured_base"; fi'
        ),
        *[f"    {line}" for line in dependency_frontier_lines],
        '    if [ "$frontier_count" = 1 ] && git -C "$target" merge-base '
        '--is-ancestor "$head_before" "$frontier_commit"; then',
        '      git -C "$target" reset --hard "$frontier_commit" >/dev/null',
        "    else",
        *[f"      {line}" for line in dependency_merge_lines],
        '      result_tree="$(git -C "$target" rev-parse HEAD^{tree})"',
        '      set -- git -C "$target" commit-tree "$result_tree" -p "$head_before"',
        *[f"      {line}" for line in dependency_parent_lines],
        '      set -- "$@" -m "$dependency_commit_message"',
        ('      max_parent_timestamp="$(git -C "$target" show -s --format=%ct "$head_before")"'),
        *[f"      {line}" for line in dependency_timestamp_lines],
        "      deterministic_timestamp=$((max_parent_timestamp + 1))",
        '      export GIT_AUTHOR_DATE="@$deterministic_timestamp +0000"',
        '      export GIT_COMMITTER_DATE="@$deterministic_timestamp +0000"',
        '      if ! composed_commit="$("$@")"; then',
        '        git -C "$target" reset --hard "$head_before" >/dev/null',
        "        exit 1",
        "      fi",
        '      git -C "$target" reset --hard "$composed_commit" >/dev/null',
        "    fi",
        '    composed_base="$(git -C "$target" rev-parse HEAD)"',
        (
            '    git -C "$target" config --worktree '
            'evander.dependencyGitProvenance "$expected_dependency_provenance"'
        ),
        ('    git -C "$target" config --worktree evander.baseCommit "$composed_base"'),
        "  fi",
        "fi",
        "fi",
        (
            'if [ -n "$configured_base" ] && ! git -C "$target" config '
            "--worktree --get evander.baseCommit >/dev/null 2>&1; then "
            'git -C "$target" config --worktree evander.baseCommit '
            '"$configured_base"; fi'
        ),
        "if [ -d .evander ]; then test ! -L .evander; fi",
        'if [ -L "$target/.evander" ]; then exit 1; fi',
        'if [ -d .evander ] && [ ! -e "$target/.evander" ]; then cp -R .evander "$target/.evander"; fi',
        "if [ -d .evander/bin ]; then",
        "  test ! -L .evander",
        "  test ! -L .evander/bin",
        '  if [ -e "$target/.evander" ]; then test ! -L "$target/.evander"; fi',
        '  install -d -m 0755 "$target/.evander"',
        '  if [ -e "$target/.evander/bin" ]; then test ! -L "$target/.evander/bin"; fi',
        '  install -d -m 0755 "$target/.evander/bin"',
        "  for helper_name in agent-pr-note push-agent sync-base; do",
        '    helper_source=".evander/bin/$helper_name"',
        '    if [ -f "$helper_source" ] && [ ! -L "$helper_source" ]; then',
        '      helper_temp="$(mktemp "$target/.evander/bin/.${helper_name}.XXXXXX")"',
        '      install -m 0755 "$helper_source" "$helper_temp"',
        '      mv -f "$helper_temp" "$target/.evander/bin/$helper_name"',
        "    fi",
        "  done",
        "fi",
        ('base_commit="$(git -C "$target" config --worktree --get evander.baseCommit)"'),
        'head_commit="$(git -C "$target" rev-parse HEAD)"',
        'composition_root_commit=""',
        'composed_commit="$head_commit"',
        'if [ "$expected_dependency_provenance" != "[]" ]; then',
        '  if [ -z "$configured_composition_root" ]; then',
        '    configured_composition_root="$head_before"',
        (
            '    git -C "$target" config --worktree '
            'evander.dependencyCompositionRootCommit "$configured_composition_root"'
        ),
        "  fi",
        '  if [ -z "$configured_composed_commit" ]; then',
        '    candidate_composed="$base_commit"',
        "    candidate_contains_dependencies=1",
        *[
            (
                f'    if ! git -C "$target" merge-base --is-ancestor '
                f'"$dependency_commit_{index}" "$candidate_composed"; then '
                "candidate_contains_dependencies=0; fi"
            )
            for index, _source in enumerate(dependency_git_sources)
        ],
        (
            '    if [ "$candidate_contains_dependencies" != 1 ] '
            '|| ! git -C "$target" merge-base --is-ancestor '
            '"$configured_composition_root" "$candidate_composed" '
            '|| ! git -C "$target" merge-base --is-ancestor '
            '"$candidate_composed" "$head_commit"; then '
            'candidate_composed="$head_commit"; fi'
        ),
        '    configured_composed_commit="$candidate_composed"',
        (
            '    git -C "$target" config --worktree '
            'evander.dependencyComposedCommit "$configured_composed_commit"'
        ),
        "  fi",
        (
            '  git -C "$target" merge-base --is-ancestor '
            '"$configured_composition_root" "$configured_composed_commit"'
        ),
        (
            '  git -C "$target" merge-base --is-ancestor '
            '"$configured_composed_commit" "$head_commit"'
        ),
        '  composition_root_commit="$configured_composition_root"',
        '  composed_commit="$configured_composed_commit"',
        "fi",
        'if [ "$head_commit" = "$base_commit" ] && ! git -C "$target" ls-remote '
        '--exit-code --heads origin "refs/heads/$branch" >/dev/null 2>&1; then',
        '  git -C "$target" push origin "HEAD:refs/heads/$branch"',
        "fi",
        *(
            [
                'git -C "$target" config --worktree author.name '
                + shlex.quote(task_author_identity.name),
                'git -C "$target" config --worktree author.email '
                + shlex.quote(task_author_identity.email),
            ]
            if task_author_identity is not None
            else []
        ),
        'echo "EVANDER_BASE_COMMIT=$base_commit"',
        'echo "EVANDER_COMPOSITION_ROOT_COMMIT=$composition_root_commit"',
        'echo "EVANDER_COMPOSED_COMMIT=$composed_commit"',
        'echo "EVANDER_COMPOSED_TREE=$(git -C "$target" rev-parse "$composed_commit^{tree}")"',
        'printf "%s\\n" "$head_commit"',
    ]
    command = "\n".join(command_lines)
    return command, clone_dir, worktree_path
