# Operations

## Host Packages

Install these packages on the Ubuntu host before using the scripts:

- `openssh-server`
- `qemu-system-x86`
- `libvirt-daemon-system`
- `libvirt-clients`
- `virtinst`
- `cloud-image-utils`
- `ovmf`
- `nftables`

## Bootstrap Flow

1. Install Python dependencies with `uv`.
2. Copy `config/gateway.example.yaml` to `config/gateway.yaml`.
3. Run `agent-sandbox-cli init-state`.
4. Create the libvirt network with `./scripts/create_libvirt_network.sh`.
5. Apply guest forwarding policy with `./scripts/apply_firewall.sh`.
6. Download the Ubuntu cloud image with `sudo ./scripts/create_base_vm.sh`.
7. Create a session manifest with `agent-sandbox-cli create-session`.
8. Launch a VM with `sudo ./scripts/launch_agent_vm.sh`.
9. Install the Codex binary into the guest with `./scripts/bootstrap_codex_vm.sh <vm-name>`.
10. Optionally install the host `.NET` SDK into the guest with `./scripts/bootstrap_dotnet_vm.sh <vm-name>`.
11. Optionally install Playwright into the guest with `./scripts/bootstrap_playwright_vm.sh <vm-name>` for rendered-DOM debugging.
12. Optionally create a local bare Git remote with `./scripts/create_git_remote.sh <repo-name>` if the VM should push code back to the host.

- The launch script resizes each overlay disk to `20G` by default.
- Override per VM with `DISK_SIZE_GB=40 sudo ./scripts/launch_agent_vm.sh ...`.

## SSH Access

- New VMs install `openssh-server`.
- The launch script injects the caller's `~/.ssh/id_ed25519.pub` by default, falling back to `~/.ssh/id_rsa.pub`.
- Override the key with `SSH_PUBLIC_KEY_FILE=/path/to/key.pub`.
- After boot, find the guest IP with `virsh domifaddr <vm-name>` and connect with `ssh agent@<vm-ip>`.

## VM Codex Configuration

- Cloud-init writes `/home/agent/.codex/config.toml` for the `agent` user.
- That config points Codex at the host relay via `openai_base_url`.
- Cloud-init also appends a shell profile hook that loads `/etc/agent-sandbox/session.env` and exports `OPENAI_API_KEY=relay-placeholder` if no key is already set.
- `./scripts/bootstrap_codex_vm.sh <vm-name>` copies the vendored Linux Codex binary from the host into `~/bin/codex` for the guest user and verifies it.
- The bootstrap script also overwrites `~/.codex/config.toml` with the relay-aware configuration and appends the placeholder-key shell hook if needed, so it corrects preexisting Codex state in the VM.
- The bootstrap script repairs `/home/agent` ownership first, so it also works against VMs created before the home directory ownership fix.
- New VMs pick this up automatically; existing VMs need to be recreated.

## VM Playwright Bootstrap

- `./scripts/bootstrap_playwright_vm.sh <vm-name>` copies a host-seeded Node runtime into `~/toolchains/node-v24.14.0`.
- It copies `playwright` and `playwright-core` into `~/playwright-runtime/node_modules`.
- It copies the host Playwright browser cache into `~/.cache/ms-playwright`.
- It appends `PATH`, `PLAYWRIGHT_BROWSERS_PATH`, and `NODE_PATH` exports to `~/.profile`.
- It verifies the guest can resolve the Chromium executable without guest internet access.

## VM .NET Bootstrap

- `./scripts/bootstrap_dotnet_vm.sh <vm-name>` copies the host `.NET` installation into `~/toolchains/dotnet`.
- It appends `DOTNET_ROOT`, `DOTNET_CLI_HOME`, and `PATH` exports to `~/.profile`.
- It verifies the guest can run `dotnet --info` without guest internet access.

## Host-Side LLM Access

- The firewall allows only the host TCP ports listed in `ALLOWED_TCP_PORTS`.
- Defaults: `22` for host SSH/Git, `8000` for the MCP gateway, and `11434` for the host-side OpenAI relay or a local OpenAI-compatible LLM endpoint such as Ollama.
- Override when applying firewall rules, for example:

  ```bash
  sudo ALLOWED_TCP_PORTS="22 8000 11434 4000" ./scripts/apply_firewall.sh
  ```

- The guest receives `AGENT_SANDBOX_LLM_BASE_URL` and `OPENAI_BASE_URL` in `/etc/agent-sandbox/session.env`.
- The scaffold includes `agent-sandbox-openai-relay`, a host-side OpenAI-compatible proxy that reads the real API key only from the host environment and forwards guest requests to `https://api.openai.com/v1`.
- For OpenAI/Codex-backed workflows while keeping guests off the public internet, run the relay on the host and leave `LLM_BASE_URL` at its default `http://192.168.250.1:11434/v1`.

## Systemd Services

Install persistent host services with:

```bash
sudo ./scripts/install_systemd_units.sh
```

This creates:

- `agent-sandbox-firewall.service`
- `agent-sandbox-gateway.service`
- `agent-sandbox-openai-relay.service`

Then:

1. Copy `config/firewall.env.example` to `config/firewall.env` if needed.
2. Copy `config/openai-relay.env.example` to `config/openai-relay.env` if needed.
3. Set the real `OPENAI_API_KEY` in `config/openai-relay.env`.
4. Start and inspect the services:

```bash
sudo systemctl start agent-sandbox-firewall.service
sudo systemctl start agent-sandbox-gateway.service
sudo systemctl start agent-sandbox-openai-relay.service
sudo systemctl status agent-sandbox-firewall.service
sudo systemctl status agent-sandbox-gateway.service
sudo systemctl status agent-sandbox-openai-relay.service
```

## Local Git Remote Flow

Create a host-local bare repository:

```bash
cp config/git-server.env.example config/git-server.env
$EDITOR config/git-server.env
./scripts/create_git_remote.sh sample-repo
```

Make sure the host SSH service is installed and reachable from the VM network:

```bash
sudo apt-get install -y openssh-server
sudo systemctl enable --now ssh
sudo ./scripts/apply_firewall.sh
```

Then in the VM, add the printed SSH remote URL and push into a session-specific namespace such as:

```bash
git push agent HEAD:refs/heads/agents/<session-id>/<branch-name>
```

Review those branches from the host and merge them into your trusted repository workflow only after review.

## Operational Guardrails

- Keep the base cloud image read-only and only boot guests from overlays.
- Destroy guests after each session.
- Store provider credentials only on the host side.
- Rotate Discord and email credentials independently from VM lifecycle.
- Review `runtime/audit.jsonl` as the source of truth for privileged activity.
- Keep VM disks and seed ISOs out of home directories when using `qemu:///system`; the default scripts now place them under `/var/lib/libvirt/images/agent-sandbox`.
