# Agent Sandbox

`agent-sandbox` is a host-side control plane for running autonomous AI agents inside disposable libvirt VMs while exposing only narrow, audited capabilities through MCP.

## What This Scaffold Includes

- A Python MCP gateway with policy-gated Discord and email tool surfaces
- A host-side OpenAI relay that keeps the API key on the host and exposes an OpenAI-compatible endpoint to guests
- A host-side NuGet relay that proxies nuget.org and private Azure Artifacts feeds for guests
- File-backed session, approval, audit, and draft storage for local development
- A small CLI for bootstrapping state and creating session manifests
- Libvirt, cloud-init, and firewall scripts for an isolated VM network
- Systemd service templates and an install script for persistent host services
- Local bare Git remote tooling for getting code changes back out of VMs
- Configuration templates and implementation notes for evolving the scaffold into a production setup

## Core Security Model

- Guests have no direct access to host secrets.
- Guests reach only the MCP gateway over a private libvirt network.
- Every tool call is checked against a session manifest.
- Irreversible actions require a separate approval record and exact payload match.
- State is append-only where possible and intentionally simple in v1.

## Quick Start

1. Install host dependencies and create a virtual environment:

   ```bash
   sudo apt install qemu-system-x86 libvirt-daemon-system libvirt-clients virtinst cloud-image-utils ovmf
   uv venv
   . .venv/bin/activate
   uv pip install -e .
   ```

2. Copy the example configuration and adjust it:

   ```bash
   cp config/gateway.example.yaml config/gateway.yaml
   ```

   For Discord bot polling/posting, switch `discord_backend` to `bot_api` and keep
   `discord_bot_token_env` set to the host environment variable that holds your bot token
   (default: `DISCORD_BOT_TOKEN`).

3. Initialize runtime state:

   ```bash
   agent-sandbox-cli init-state --config config/gateway.yaml
   ```

4. Start the MCP gateway:

   ```bash
   agent-sandbox-gateway --config config/gateway.yaml
   ```

5. Create the libvirt network and firewall policy:

   ```bash
   sudo ./scripts/create_libvirt_network.sh
   sudo ./scripts/apply_firewall.sh
   ```

   The default firewall policy allows host ports `22`, `8000`, `11434`, and `11666` so VMs can use SSH for Git remotes, MCP on `8000`, the OpenAI relay on `11434`, and the NuGet relay on `11666`.

6. Start the host-side services:

   ```bash
   export OPENAI_API_KEY=...
   export DISCORD_BOT_TOKEN=...
   agent-sandbox-gateway --config config/gateway.yaml
   agent-sandbox-openai-relay --config config/gateway.yaml
   ```

7. Download the base image and launch VMs from a libvirt-accessible storage path:

   ```bash
   sudo ./scripts/create_base_vm.sh
   sudo ./scripts/launch_agent_vm.sh vm-real real-session
   ```

   By default, the launch script injects your host `~/.ssh/id_ed25519.pub` key into the guest and exposes a host-side LLM base URL of `http://192.168.250.1:11434/v1`. Override with `SSH_PUBLIC_KEY_FILE=...` or `LLM_BASE_URL=...` if needed.
   New VMs default to a `20G` virtual disk; override with `DISK_SIZE_GB=...` before launch if needed.
   New VMs also receive `/home/agent/.codex/config.toml` and a shell profile hook so Codex defaults to the host relay without the deprecated `OPENAI_BASE_URL` warning.

8. Install the Codex binary into the VM:

   ```bash
   ./scripts/bootstrap_codex_vm.sh vm-real
   ```

   This copies the vendored Linux Codex binary from the host install into the guest user's `~/bin`, rewrites `~/.codex/config.toml` to use the host relay, ensures the placeholder API-key shell hook exists, and verifies `codex --version`.

9. Optionally install the host `.NET` SDK into the VM:

   ```bash
   ./scripts/bootstrap_dotnet_vm.sh vm-real
   ```

   This copies the host `.NET` installation into the guest, appends `DOTNET_ROOT`, `DOTNET_CLI_HOME`, and `PATH` exports to the guest profile, and verifies `dotnet --info`.

10. Optionally install host-relay NuGet sources into the VM:

   ```bash
   ./scripts/bootstrap_nuget_vm.sh vm-real
   ```

   This writes `~/.nuget/NuGet/NuGet.Config` in the guest so package restore can use host relay endpoints for nuget.org and Azure Artifacts.

11. Optionally install Playwright into the VM for frontend debugging:

   ```bash
   ./scripts/bootstrap_playwright_vm.sh vm-real
   ```

   This copies a host-seeded Node runtime, Playwright packages, and browser cache into the guest and verifies that Playwright can resolve Chromium offline.

## Persistent Host Services

Install the gateway and relay services:

```bash
sudo ./scripts/install_systemd_units.sh
$EDITOR config/firewall.env
$EDITOR config/openai-relay.env
$EDITOR config/nuget-relay.env
sudo systemctl start agent-sandbox-firewall.service
sudo systemctl start agent-sandbox-gateway.service
sudo systemctl start agent-sandbox-openai-relay.service
sudo systemctl start agent-sandbox-nuget-relay.service
```

The generated units run as your user where appropriate and read environment files from `config/`.

## Local Git Remotes

Use the included bare-repo helper when you want VMs to push code changes back to the host over the private network:

```bash
cp config/git-server.env.example config/git-server.env
$EDITOR config/git-server.env
./scripts/create_git_remote.sh sample-repo
```

See [git-remotes.md](/home/jonathan/agent-sandbox/docs/git-remotes.md) for the review flow and branch naming pattern.

## Discord Adapter Configuration

- Required host-side env var for Discord bot API mode: `DISCORD_BOT_TOKEN` (or the name in
  `discord_bot_token_env`).
- Optional host-side env vars for other host services:
  - `OPENAI_API_KEY` for the OpenAI relay.
  - `AZURE_ARTIFACTS_PAT` for private Azure Artifacts feeds through the NuGet relay.
- Session Discord channel allowlists are set when creating a session:

  ```bash
  agent-sandbox-cli create-session \
    real-session \
    --config config/gateway.yaml \
    --discord-channel 123456789012345678 \
    --discord-channel 234567890123456789
  ```

- Operator user allowlist is configured globally on the host in `config/gateway.yaml` with
  `discord_operator_user_allowlist`.
- Guests never need direct Discord credentials or internet access; Discord API access happens only
  in the host-side gateway process.

## Project Layout

- `src/agent_sandbox/`: gateway, relay, policy, storage, and CLI code
- `scripts/`: libvirt and firewall automation
- `config/`: sample configuration and cloud-init template
- `systemd/`: service templates for the host gateway, relay, and firewall policy
- `docs/`: operating notes and next steps

## Current Limitations

- Email is still a stub provider behind a local JSON store.
- Discord has both `stub` and `bot_api` backends; production deployments should enforce strict
  channel and operator allowlists.
- The gateway is ready for integration adapters, not production credentials by default.
- Network/firewall scripts assume Ubuntu 24.04 and nftables-backed libvirt hosts.
- VM disk and seed artifacts default to `/var/lib/libvirt/images/agent-sandbox`, so VM lifecycle scripts should be run with `sudo` unless you deliberately provision an alternate libvirt-accessible storage path.
- Guests can reach only the specific host TCP ports you allow in `ALLOWED_TCP_PORTS`; the defaults are `22` for host SSH/Git, `8000` for MCP, `11434` for the host-side OpenAI relay or another OpenAI-compatible LLM endpoint, and `11666` for the host-side NuGet relay.
