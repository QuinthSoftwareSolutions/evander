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
sudo install -d -m 0755 /var/lib/libvirt/images/agent-sandbox/images
sudo cp ~/Downloads/dotnet-agent.qcow2 /var/lib/libvirt/images/agent-sandbox/images/
sudo cp ~/Downloads/ubuntu-24.04-server-cloudimg-amd64.img /var/lib/libvirt/images/agent-sandbox/images/
sudo qemu-img info /var/lib/libvirt/images/agent-sandbox/images/dotnet-agent.qcow2
```

Expected checks:

- `qemu-img info` reports `file format: qcow2`
- the image is cloud-init-compatible and boots to an SSH-ready guest
- no secrets, tokens, or host-specific private keys are baked into the image

If you need a different image name, copy it into
`/var/lib/libvirt/images/agent-sandbox/images/` with `sudo cp` and verify it with
`qemu-img info` before registering an alias.

## Register An Alias

Directory-scoped `vm.json` stores the alias catalog. Register an approved alias at `root` and
confirm the effective config before using it:

```bash
agent-sandbox-cli directories config-set root '{"libvirt_vm_default_template":"default","libvirt_vm_templates":{"default":"ubuntu-24.04-server-cloudimg-amd64.img","dotnet-lab":"dotnet-agent.qcow2"}}' --domain vm --runtime-dir runtime
agent-sandbox-cli directories config-show root --domain vm --runtime-dir runtime
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
`/var/lib/libvirt/images/agent-sandbox/vms/<vm-name>/disk.qcow2` and uses the selected base image
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
   agent-sandbox-cli directories config-show root --domain vm --runtime-dir runtime
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
   sudo qemu-img info /var/lib/libvirt/images/agent-sandbox/vms/vm-template-smoke/disk.qcow2
   ```

   The output should report a backing file under
   `/var/lib/libvirt/images/agent-sandbox/images/dotnet-agent.qcow2`.

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

## When To Use A VM Template

Use a VM template when the guest needs OS-level or image-level changes such as:

- a different kernel or distro base
- drivers or low-level packages that are expensive to bootstrap repeatedly
- offline OS customization that should exist before the first boot

Use bootstrap flags on `effect:agent:create` for lighter, post-launch capabilities such as
`dotnet`, `nuget`, `mssql`, `playwright`, and `codex_skills`.

If the change is only an optional toolchain or guest profile tweak, prefer bootstrap over creating
another VM template.
