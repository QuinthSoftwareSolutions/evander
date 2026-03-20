# Architecture Notes

## Components

- Host orchestrator: creates session manifests, launches VMs, and destroys them at the end of a run.
- MCP gateway: the only host service reachable from guests.
- OpenAI relay: a separate host service that exposes an OpenAI-compatible `/v1` endpoint to guests while keeping the real API key on the host.
- Local bare Git remote: a host-side code transfer path that lets guests push commits back over the private network.
- Disposable guests: run agent code and call the MCP gateway over the isolated libvirt network.
- Audit store: append-only JSONL log for every privileged tool attempt.

## Why This Shape

- `KVM + libvirt` is the practical default for Ubuntu 24.04.
- Guests have no direct internet path, which makes MCP the explicit capability boundary.
- Guests use the host relay for model inference instead of holding cloud API credentials directly.
- Git handles code transport; MCP remains the control and policy path.
- Session manifests are the smallest useful policy primitive for v1.
- Approval records let you keep autonomy for drafts and read operations while gating sends.

## Planned Next Integrations

- Replace the stub email service with a provider-backed adapter that preserves the same tool contract.
- Replace the stub Discord service with a bot adapter that checks a fixed channel or DM.
- Add a separate admin channel for approving requests without granting the guest broader power.
