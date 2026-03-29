# Local Git Remotes

## Goal

Move code changes out of an agent VM without routing source code through MCP payloads.

## Recommended Shape

- Keep code transfer on Git, not MCP.
- Host a bare repository on the host-only network.
- Let the VM push only to that internal remote.
- Review and merge from the host after the agent finishes.

## Setup

Create a local bare remote on the host:

```bash
cd /home/jonathan/agent-sandbox
agent-sandbox-cli directories config-set --domain issue_setup root '{"git_root": "/home/jonathan/agent-sandbox/git-remotes", "git_host": "192.168.250.1", "git_user": "jonathan", "git_ssh_alias": "agent-sandbox-git-host"}'
./scripts/create_git_remote.sh sample-repo
```

This creates a bare repository under `AGENT_SANDBOX_GIT_ROOT` and prints the SSH remote URL for the VM.
Before a guest can clone or push without prompting for your host password, bootstrap guest Git SSH once:

```bash
./scripts/bootstrap_git_vm.sh vm-real
```

That creates a dedicated guest SSH key, installs a restricted forced-command entry in your host
`authorized_keys`, and configures the guest to use the host alias
`agent-sandbox-git-host` by default.

If you want to mirror the default host repo into an issue-specific bare remote using the project
naming convention, use:

```bash
./scripts/mirror_issue_repo.sh 14084
```

Freshness controls:

- local source path: defaults to no upstream fetch
- remote URL source: always refreshes latest refs in `runtime/repo-cache` first
- `--sync-remote`: fetch `--prune <remote-name>` for local source before mirroring
- `--force-local`: continue even if local default branch is behind remote default branch
- optional pinning: `--branch <name>` or `--refspec <src:dst>`

Examples:

```bash
./scripts/mirror_issue_repo.sh --sync-remote --remote-name origin 14084 /path/to/local/repo
./scripts/mirror_issue_repo.sh 14084 https://example.com/org/repo.git
./scripts/mirror_issue_repo.sh --branch main 14084 /path/to/local/repo
```

If you want the shortest host-side setup path for an issue VM, combine both steps:

```bash
./scripts/setup_issue_git_vm.sh vm-real 14084
```

`setup_issue_git_vm.sh` forwards the same freshness and pinning flags to
`mirror_issue_repo.sh`, and `setup_issue_vm.sh` forwards them again for the full bring-up path.

That bootstraps guest Git SSH and refreshes the `14084_repo.git` host mirror in one command.

That derives:

- bare repo: `14084_repo.git`
- host working-tree remote: `14084-mirror`

It uses the explicit second argument when provided, otherwise
`AGENT_SANDBOX_DEFAULT_REPO_SOURCE`. Pass a repo path or remote URL that should be mirrored.

Each bare repo also gets a `post-receive` hook. Once the bare repo has been associated with a
trusted host clone by `mirror_issue_repo.sh`, that hook can automatically promote guest-pushed
`agents/...` branches, push a review branch to `origin`, create or reuse the upstream PR, and emit
a host-side notification.

Mirror runs emit an audit summary including source mode, effective default/base branch, and source
commit SHA used for the mirror update.

Issue mirrors also persist their mirror-source settings in the bare repo config. When a guest fetches
or pulls from an issue mirror over the restricted SSH alias, the host now refreshes that mirror from
its configured source immediately before serving `git-upload-pack`. In practice, `git pull` inside the
guest can bring the host mirror up to date with the canonical repo and then pull those latest refs
into the guest in one step. If the host-side refresh fails, the guest fetch/pull fails rather than
silently serving stale refs.

## VM Workflow

Inside the VM:

```bash
git clone ssh://agent-sandbox-git-host/home/jonathan/agent-sandbox/git-remotes/sample-repo.git
cd sample-repo
git remote add agent ssh://agent-sandbox-git-host/home/jonathan/agent-sandbox/git-remotes/sample-repo.git
git checkout -b work
# agent edits, commits, tests
git push agent HEAD:refs/heads/agents/<session-id>/work-<agent-suffix>
```

For an issue-specific mirrored repo, the guest clone typically looks like:

```bash
git clone ssh://agent-sandbox-git-host/home/jonathan/agent-sandbox/git-remotes/14084_repo.git
```

Recommended branch namespace:

- `agents/<session-id>/<branch-name>-<agent-suffix>`

## Review Flow

On the host:

```bash
./scripts/promote_agent_branch.sh /path/to/trusted-clone sample-repo agents/<session-id>/work-<agent-suffix>
```

That fetches `refs/heads/agents/<session-id>/work-<agent-suffix>` from the quarantine bare repo into the
trusted working clone, creates a local review branch like `review/<session-id>/work-<agent-suffix>`, and leaves
`origin` untouched until you decide the change is acceptable.

Then review and validate in the trusted clone:

```bash
cd /path/to/trusted-clone
git status
git log --oneline --decorate -n 5
git diff origin/main...HEAD
```

When the branch is ready for the canonical remote, push it from the trusted clone and optionally
open the PR:

```bash
./scripts/open_agent_pr.sh /path/to/trusted-clone review/<session-id>/work
./scripts/open_agent_pr.sh --create-pr --base main /path/to/trusted-clone review/<session-id>/work
```

This keeps the flow safe:

- VM pushes only to the quarantine bare repo
- the host promotes a specific agent branch into a review branch
- only the reviewed branch is pushed to `origin`
- PR creation is optional and happens only from the trusted clone

## Automatic Promotion and PR Creation

For issue-specific mirrors created through `setup_issue_git_vm.sh` or `mirror_issue_repo.sh`,
guest pushes to `refs/heads/agents/<session-id>/<branch>` can trigger host-side automation:

- fetch that agent branch into the trusted clone
- update or create `review/<session-id>/<branch>`
- push the review branch to `origin`
- create or reuse the upstream PR
- append an event to `runtime/agent-events.log`
- attempt a desktop notification with `notify-send`

### PR body assembly from Git notes

Host-side PR automation reads agent-authored markdown summaries from the dedicated notes ref
`refs/notes/agent-pr` (the short ref name is `agent-pr` in `git notes --ref=agent-pr ...` commands).
The automation fetches that notes ref before it calls `open_agent_pr.sh`, then tries to read the
note attached to the pushed commit SHA only.

Guest VMs should treat that tip note as the canonical full PR summary snapshot for the current
branch, not as a per-commit delta log. `setup_issue_vm.sh` installs a `git agent-pr-note` helper
that keeps a branch-local aggregate cache, rewrites the full note onto each new `HEAD`, and lets
agents append later updates without losing earlier summary content.

When a PR body is assembled, merge order is deterministic:

1. optional fixed operator-controlled body (configured by `AGENT_SANDBOX_PR_FIXED_BODY_FILE`)
2. separator + heading (`---` and `## Agent Summary`)
3. appended agent summary markdown from the commit note in `refs/notes/agent-pr`

For per-directory PR boilerplate, use the directory-scoped `pr_template` domain and migrate the
legacy repo-level `config/pr-template.md` into root config with
`agent-sandbox-cli migrate-pr-template-to-root --config config/gateway.yaml`.

Fallback behavior when the note is missing:

- If no note exists for the pushed commit, PR creation continues without the note content.
- If a fixed body file is configured, that fixed body is used by itself.
- If no fixed body is configured and no note is available, PR tooling falls back to its default body behavior.

### Security / validation note

Treat note content as **untrusted agent input**. The host flow passes it through as markdown text
for PR descriptions only; operators should not shell-interpolate, eval, or execute note text.
Keep usage to display/render contexts (for example, PR body markdown) rather than command contexts.

For Azure DevOps remotes, set the PR PAT in the environment:

```bash
AZURE_DEVOPS_PR_PAT=...
```

`open_agent_pr.sh` detects Azure DevOps automatically from the trusted clone's `origin` URL and
creates the PR there. Pass `--provider azure_devops` if you need to override provider detection
explicitly. The same helper still supports GitHub remotes through `gh`.

## Notes

- This assumes the VM can SSH to the host over the private libvirt network.
- `bootstrap_git_vm.sh` avoids giving guests your host password and restricts the installed SSH key
  to Git transport commands only.
- `promote_agent_branch.sh` intentionally refuses to overwrite an existing local review branch.
- `open_agent_pr.sh` uses a normal push for non-review branches.
- For `review/*` branches it uses `--force-with-lease` after first refreshing the exact remote-tracking ref on the push remote, so reused review branches stay safe without stale-lease failures.
- For GitHub canonical remotes, automated `open_agent_pr.sh --create-pr` runs prefer HTTPS plus `gh auth git-credential` when `gh auth status` succeeds. This is more reliable for post-receive hooks than SSH because hook environments are often non-interactive and may not inherit a usable `ssh-agent` socket or your full SSH config.
- Use a separate dropbox bare repo for agent output if you do not want VMs pushing directly to your main internal remote.
- MCP remains useful for notifications, approval gates, and branch metadata, but not for bulk source transfer.

## Feedback + Reply Integration Checklist (with Git promotion)

When combining Git push automation with PR feedback handling:

### Setup steps

1. Complete Git remote setup (`bootstrap_git_vm.sh` + mirror/setup issue helper).
2. Ensure the host heartbeat worker service is running so feedback polling runs continuously (`sudo systemctl status agent-sandbox-host-heartbeat.service`).
3. Ensure session policy allows feedback tools for the target provider/repo/PR stream.
4. In agent logic, post replies with `post_pr_feedback_reply(..., idempotency_key=...)` before `git push-agent` when feedback requires code changes.

### Expected timing behavior

- Git push/promotion remains event-driven (on push hooks).
- Feedback pickup is host-worker-driven based on configured poll interval/jitter/cooldown, so comments may take up to one poll interval before updates begin.
- The host worker only updates feedback state; guest execution remains explicit and idempotent via MCP tool flow.
- `bootstrap_codex_vm.sh` defaults `ENABLE_GUEST_HEARTBEAT=0`, so new guests keep
  `agent-heartbeat.timer` disabled unless explicitly enabled for that agent.
- If you enable the guest timer, set `GUEST_HEARTBEAT_PROMPT` when bootstrapping to control the
  resume prompt used by `codex exec resume --last`.
- Migration note for older guests: disable the legacy timer with
  `systemctl --user disable --now agent-heartbeat.timer || true` to prevent double wakes.

### Troubleshooting

- **Host heartbeat lock contention:** inspect host service status and lock path if `host-heartbeat` exits immediately.
- **Provider reply API errors (GitHub/Azure DevOps):** keep the same `idempotency_key` on retry so successful-but-unconfirmed replies are deduped.
- **Push worked but no host promotion/PR update:** inspect bare-repo hook output and `runtime/agent-events.log`.
- **`AGENT_PR_NOTES fetch failed (non-fatal)` in `runtime/agent-events.log`:**
  host could not fetch `refs/notes/agent-pr` from the bare remote during promotion.
  Verify the guest push included `refs/notes/agent-pr` and the trusted clone can fetch that ref.
- **`AGENT_PR_NOTES read failed (non-fatal)` in `runtime/agent-events.log`:**
  notes ref was present but reading the note for the promoted commit failed.
  Re-attach the cached summary to the exact tip commit with `git agent-pr-note ensure-head`, then
  push again with `git push-agent`.
- **`AGENT_PR_NOTES sha resolution failed (non-fatal)` in `runtime/agent-events.log`:**
  promoted SHA could not be resolved to a commit in the trusted clone.
  Check post-receive input/new SHA correctness and confirm the promote fetch succeeded for the pushed branch.
- **`Configured PR fixed body file missing (non-fatal)` in `runtime/agent-events.log`:**
  configured fixed boilerplate file path does not exist on the host.
  Fix `AGENT_SANDBOX_PR_FIXED_BODY_FILE` or create the file, then push a new commit to retrigger automation.

### Audit/log locations and healthy signals

- Host Git automation log: `runtime/agent-events.log`.
- Host feedback state: `runtime/pr-feedback-store.json`, `runtime/pr-reply-idempotency.json`.
- Auto-PR body composition flags are appended to each promotion event as key-value pairs:
  - `used_fixed_body=0|1`
  - `used_agent_note=0|1`
  - `body_truncated=0|1`
  - Example audit command:
    ```bash
    rg "Agent branch promoted.*used_fixed_body=.*used_agent_note=.*body_truncated=" runtime/agent-events.log
    ```
- Healthy pattern:
  - feedback appears once in store,
  - reply retries collapse to deduplicated result,
  - resulting commit lands on `agents/<session-id>/<branch>` and is promoted to `review/<session-id>/<branch>`.
