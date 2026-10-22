#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
destination="${1:-${root}/dist}"
mkdir -p "${destination}"
staging="$(mktemp -d)"
trap 'rm -rf "${staging}"' EXIT
cp "${root}/package.json" "${root}/package-lock.json" "${staging}/"
npm ci --ignore-scripts --no-audit --no-fund --prefix "${staging}"
cp "${root}/evander_codex_security_addon/"{runner.py,codex_bridge.py,configure.mjs,coverage_runner.py,coverage_sdk.mjs,coverage_projection.py} "${staging}/"
cp "${HOST_NODE_BINARY:-$(command -v node)}" "${staging}/node"
cat >"${staging}/codex" <<'SHIM'
#!/bin/sh
exec /usr/bin/python3 /opt/evander/codex-security/codex_bridge.py "$@"
SHIM
chmod -R go-w "${staging}"
chmod 0755 "${staging}" "${staging}/node" "${staging}/codex"
tar -C "${staging}" -czf "${destination}/codex-security-runtime.tar.gz" .
sha256sum "${destination}/codex-security-runtime.tar.gz" >"${destination}/codex-security-runtime.tar.gz.sha256"
echo "${destination}/codex-security-runtime.tar.gz"
