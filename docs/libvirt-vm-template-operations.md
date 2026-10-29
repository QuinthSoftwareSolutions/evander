# Libvirt VM Template Operations

This runbook covers libvirt VM template selection for `effect:agent:create` and the host launch
path that consumes it.

Use this when you need to prepare or register a cloud-init-compatible base image for a libvirt
agent VM. This is not a Docker image workflow. `container_image` is the selector for
`container_worker_vm`; `vm_image` is the libvirt-only selector for approved VM templates.

## Terms

- VM template or base image: a cloud-init-compatible `qcow2` image used as the read-only backing
  image for each per-agent overlay disk.
- Docker or container image: the runtime image selected with `container_image` for
  `container_worker_vm`.
- Alias: an operator-approved key in `libvirt_vm_templates`.
- Image ref: an absolute path or a name resolved under `${VM_STORAGE_DIR}/images` by
  `scripts/launch_agent_vm.sh`.

`scripts/launch_agent_vm.sh` resolves relative image refs under
`${VM_STORAGE_DIR}/images`, uses `LIBVIRT_BASE_IMAGE` when provided, and falls back to
`ubuntu-24.04-server-cloudimg-amd64.img` when no explicit selection is present.

## Prepare A Base Image

Use Ubuntu or another cloud-init-compatible `qcow2` image. Keep the image in the shared libvirt
storage tree, not in a home directory.

```bash
sudo install -d -m 0755 /var/lib/libvirt/images/evander/images
sudo cp ~/Downloads/dotnet-agent.qcow2 /var/lib/libvirt/images/evander/images/
sudo cp ~/Downloads/ubuntu-24.04-server-cloudimg-amd64.img /var/lib/libvirt/images/evander/images/
sudo qemu-img info /var/lib/libvirt/images/evander/images/dotnet-agent.qcow2
```

Expected checks:

- `qemu-img info` reports `file format: qcow2`
- the image is cloud-init-compatible and boots to an SSH-ready guest
- no secrets, tokens, or host-specific private keys are baked into the image

If you need a different image name, copy it into
`/var/lib/libvirt/images/evander/images/` with `sudo cp` and verify it with
`qemu-img info` before registering an alias.

## Register An Alias

Directory-scoped `vm.json` stores the alias catalog. Register an approved alias at `root` and
confirm the effective config before using it:

```bash
evander-cli directories config-set root '{"libvirt_vm_default_template":"default","libvirt_vm_templates":{"default":"ubuntu-24.04-server-cloudimg-amd64.img","dotnet-lab":"dotnet-agent.qcow2"}}' --domain vm --runtime-dir runtime
evander-cli directories config-show root --domain vm --runtime-dir runtime
```

The example above keeps `default` mapped to the stock Ubuntu image and adds a
`dotnet-lab` alias for a second approved `qcow2` image.

## Select The Template

Use `effect:agent:create` with `execution_backend: "libvirt_vm"` and set `vm_image` to the alias,
not to a Docker image name.

```json
{
  "execution_backend": "libvirt_vm",
  "vm_name": "vm-template-smoke",
  "session_id": "template-smoke",
  "directory_path": "root",
  "vm_image": "dotnet-lab"
}
```

When the effect runs successfully, the output includes `vm_image` and `vm_image_alias`, and the
session manifest records the resolved selection under `metadata.libvirt_vm_template`.

Inspect the recorded selection with:

```bash
jq '.["template-smoke"].metadata.libvirt_vm_template' runtime/sessions.json
```

## Launch Path

`scripts/setup_vm.sh` passes the chosen base image through `LIBVIRT_BASE_IMAGE` to
`scripts/launch_agent_vm.sh`. The launch script then creates the per-VM overlay disk under
`/var/lib/libvirt/images/evander/vms/<vm-name>/disk.qcow2` and uses the selected base image
as the qcow2 backing file.

That means the operator prepares one approved read-only image, registers it once in `vm.json`, and
lets every VM get its own writable overlay.

## Smoke A Non-Default Alias

Use this path to prove that a non-default template alias actually launches a libvirt-backed agent
session. It requires a libvirt-capable host.

1. Destroy any stale VM from an earlier attempt:

   ```bash
   sudo ./scripts/destroy_agent_vm.sh vm-template-smoke || true
   ```

2. Confirm the alias catalog still resolves to the approved qcow2 image:

   ```bash
   evander-cli directories config-show root --domain vm --runtime-dir runtime
   ```

   The expected effective mapping includes:

   - `libvirt_vm_default_template: default`
   - `libvirt_vm_templates.default: ubuntu-24.04-server-cloudimg-amd64.img`
   - `libvirt_vm_templates.dotnet-lab: dotnet-agent.qcow2`

3. Run a temporary workflow that contains a single `effect:agent:create` node and set these
   fields on that node:

   ```json
   {
     "execution_backend": "libvirt_vm",
     "vm_name": "vm-template-smoke",
     "session_id": "template-smoke",
     "directory_path": "root",
     "vm_image": "dotnet-lab"
   }
   ```

   Run the workflow once from Admin Web or your local workflow editor. The node output should show
   `vm_image` and `vm_image_alias` when those fields are visible in the UI.

4. Verify the host-side session metadata shows the resolved image selection:

   ```bash
   jq '."template-smoke".metadata.libvirt_vm_template' runtime/sessions.json
   ```

   Expected result:

   ```json
   {"image_ref":"dotnet-agent.qcow2","template_alias":"dotnet-lab"}
   ```

5. Verify the overlay disk uses that base image as its backing file:

   ```bash
   sudo qemu-img info /var/lib/libvirt/images/evander/vms/vm-template-smoke/disk.qcow2
   ```

   The output should report a backing file under
   `/var/lib/libvirt/images/evander/images/dotnet-agent.qcow2`.

6. Check guest readiness once the VM is up:

   ```bash
   virsh domifaddr vm-template-smoke
   ssh agent@<vm-ip> 'whoami'
   ```

   If the smoke includes provider bootstrap, finish with:

   ```bash
   ./scripts/bootstrap_codex_vm.sh vm-template-smoke
   ```

7. Clean up the temporary VM after the smoke:

   ```bash
   sudo ./scripts/destroy_agent_vm.sh vm-template-smoke
   ```

   If you saved a temporary workflow draft just for this smoke, remove or revert it after the
   checks complete.

## Troubleshooting

Start with the effective alias catalog and the recorded session metadata before changing anything:

```bash
evander-cli directories config-show root --domain vm --runtime-dir runtime
jq '."template-smoke".metadata.libvirt_vm_template' runtime/sessions.json
```

If the VM is already running, `virsh domifaddr <vm-name>` tells you whether the guest got an IP,
and `sudo virsh console <vm-name>` lets you see cloud-init and boot-time failures directly.

### Missing Base Image

- Symptom: `scripts/launch_agent_vm.sh` prints `Selected libvirt base image: ...` and then
  fails with `Missing base image: ...`.
- Where to look: `evander-cli directories config-show ... --domain vm --runtime-dir runtime`,
  `sudo qemu-img info <image-ref>`, and `runtime/sessions.json` if the selection came from a
  workflow run.
- Likely cause: the alias or selector points at a file that does not exist, or the image was
  copied somewhere outside `/var/lib/libvirt/images/evander/images/`.
- Remediation: place the approved `qcow2` under the shared libvirt image tree with `sudo install
  -d -m 0755 /var/lib/libvirt/images/evander/images` and `sudo cp <source> ...`, then update
  the alias catalog or selection to point at the copied file. Re-run `sudo qemu-img info` and
  confirm it reports `file format: qcow2`.

### Unknown Alias

- Symptom: the effect runtime rejects the selector with `unknown libvirt VM template selector ...;
  configured aliases: ...`.
- Where to look: `evander-cli directories config-show root --domain vm --runtime-dir runtime`,
  the workflow run output, and `journalctl -u evander-effect-orchestrator.service` when the
  failure surfaced through `effect:agent:create`.
- Likely cause: `vm_image` names an alias that is not present in the effective `vm.json`
  catalog, or a parent directory override removed the alias you expected.
- Remediation: register the alias with `evander-cli directories config-set ... --domain vm
  --runtime-dir runtime`, or choose an existing alias. If you want the default image, omit
  `vm_image` and let `libvirt_vm_default_template` resolve it.

### Image Not Cloud-Init-Compatible

- Symptom: the VM appears in libvirt, but the guest never reaches a usable SSH state or the boot
  log shows cloud-init failures.
- Where to look: `virsh domifaddr <vm-name>`, `sudo virsh console <vm-name>`, and the guest log
  `/var/log/cloud-init-output.log`.
- Likely cause: the image is not actually cloud-init-compatible, does not contain `openssh-server`,
  or does not accept the injected authorized key.
- Remediation: start from a supported Ubuntu or otherwise cloud-init-compatible `qcow2`, verify it
  boots cleanly under libvirt, and only then register it as an alias.

### SSH Readiness Timeout

- Symptom: `scripts/lib/vm_network.sh` reports `Timed out waiting for SSH on 'agent@<ip>' ...`.
- Where to look: `virsh domifaddr <vm-name>`, `sudo virsh console <vm-name>`, and the guest
  `/var/log/cloud-init-output.log`.
- Likely cause: the guest never started `sshd`, cloud-init never finished applying the key, or the
  VM is still stuck in early boot.
- Remediation: fix the base image or cloud-init data first. Do not treat the timeout as a reason to
  add a new template or raise the timeout defaults until the image has proved it can boot and
  accept the injected SSH key.

### Bootstrap Failure

- Symptom: `scripts/setup_vm.sh` reaches `bootstrap_<provider>` and exits non-zero, or the provider
  bootstrap script emits an error after SSH is already ready.
- Where to look: `journalctl -u evander-effect-orchestrator.service` if the VM was launched
  from a workflow, `runtime/sessions.json`, the root-owned logs under
  `/var/log/evander/agent-runs`, and the guest `/var/log/cloud-init-output.log`.
- Likely cause: the optional provider bootstrap is missing a prerequisite, or the image needs a
  heavy OS-level dependency that should have been present before first boot.
- Remediation: fix the bootstrap script or the optional capability flags first. Only move a change
  into a template when it is genuinely OS-level, offline, or too expensive to bootstrap on every
  session.

## Template vs Bootstrap

Keep light capabilities as bootstrap or profile flags on `effect:agent:create`:

- `dotnet`
- `nuget`
- `mssql`
- `playwright`
- `codex_skills`

Those switches are for optional toolchains and guest profile tweaks that can be installed after the
guest is reachable. They should not force a new VM template by default.

Use a VM template when the guest needs OS-level or image-level changes such as:

- a different kernel or distro base
- drivers or low-level packages that are expensive to bootstrap repeatedly
- offline OS customization that should exist before the first boot

Do not create a new template just to avoid a normal bootstrap or to bake in a small dependency that
already fits one of the existing flags. Each new template increases maintenance, increases the
chance of alias drift, and makes the selector catalog harder to reason about.

If the requested change can be expressed as a bootstrap/profile flag, keep it there. If it requires
an OS baseline that must be present before the first boot, move it into a VM template and keep the
approved alias catalog small.
