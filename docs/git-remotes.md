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

## VM Workflow

Inside the VM:

```bash
git clone ssh://jonathan@192.168.250.1/home/jonathan/agent-sandbox/git-remotes/sample-repo.git
cd sample-repo
git remote add agent ssh://jonathan@192.168.250.1/home/jonathan/agent-sandbox/git-remotes/sample-repo.git
git checkout -b work
# agent edits, commits, tests
git push agent HEAD:refs/heads/agents/<session-id>/work
```

Recommended branch namespace:

- `agents/<session-id>/<branch-name>`

## Review Flow

On the host:

```bash
git clone /home/jonathan/agent-sandbox/git-remotes/sample-repo.git review-sample
cd review-sample
git branch -a
git checkout agents/<session-id>/work
```

Then cherry-pick, merge, or export patches into your main repository workflow after review.

## Notes

- This assumes the VM can SSH to the host over the private libvirt network.
- Use a separate dropbox bare repo for agent output if you do not want VMs pushing directly to your main internal remote.
- MCP remains useful for notifications, approval gates, and branch metadata, but not for bulk source transfer.
