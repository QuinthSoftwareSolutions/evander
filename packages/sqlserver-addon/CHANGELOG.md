# Changelog

## 0.1.1 - 2026-10-26

- Keep independent cleanup running between five-second passes, with retry and graceful shutdown. Preserve the single-pass `--reap` command.
- Defer control-plane imports on worker startup. Replace the reaper timer with a persistent service; use the reviewed CI/CD migration with Evander 0.131.2 or later.

## 0.1.0

- Registered-service management, bounded data tools, disposable leases, optional consumer authority, worker recovery and operator surfaces.
