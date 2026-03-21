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
cp config/git-server.env.example config/git-server.env
$EDITOR config/git-server.env
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

If you want the shortest host-side setup path for an issue VM, combine both steps:

```bash
./scripts/setup_issue_git_vm.sh vm-real 14084
```

That bootstraps guest Git SSH and refreshes the `14084_repo.git` host mirror in one command.

That derives:

- bare repo: `14084_repo.git`
- host working-tree remote: `14084-mirror`

By default it mirrors `/home/jonathan/Documents/Repos/ExampleIndustryAPP`, but you can pass a second
argument to target a different host repo.

## VM Workflow

Inside the VM:

```bash
git clone ssh://agent-sandbox-git-host/home/jonathan/agent-sandbox/git-remotes/sample-repo.git
cd sample-repo
git remote add agent ssh://agent-sandbox-git-host/home/jonathan/agent-sandbox/git-remotes/sample-repo.git
git checkout -b work
# agent edits, commits, tests
git push agent HEAD:refs/heads/agents/<session-id>/work
```

For an issue-specific mirrored repo, the guest clone typically looks like:

```bash
git clone ssh://agent-sandbox-git-host/home/jonathan/agent-sandbox/git-remotes/14084_repo.git
```

Recommended branch namespace:

- `agents/<session-id>/<branch-name>`

## Review Flow

On the host:

```bash
./scripts/promote_agent_branch.sh /path/to/trusted-clone sample-repo agents/<session-id>/work
```

That fetches `refs/heads/agents/<session-id>/work` from the quarantine bare repo into the
trusted working clone, creates a local review branch like `review/<session-id>/work`, and leaves
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

## Notes

- This assumes the VM can SSH to the host over the private libvirt network.
- `bootstrap_git_vm.sh` avoids giving guests your host password and restricts the installed SSH key
  to Git transport commands only.
- `promote_agent_branch.sh` intentionally refuses to overwrite an existing local review branch.
- `open_agent_pr.sh` never force-pushes; it only does a normal push to `origin`.
- Use a separate dropbox bare repo for agent output if you do not want VMs pushing directly to your main internal remote.
- MCP remains useful for notifications, approval gates, and branch metadata, but not for bulk source transfer.
