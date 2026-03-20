#!/usr/bin/env bash
set -euo pipefail

TABLE="inet agent_sandbox"
CHAIN="forward"
BRIDGE_IF="virbr250"
GATEWAY_IP="192.168.250.1"
ALLOWED_TCP_PORTS="${ALLOWED_TCP_PORTS:-8000 11434}"

if ! command -v nft >/dev/null 2>&1; then
  echo "nft is required" >&2
  exit 1
fi

if nft list table "${TABLE%% *}" "${TABLE##* }" >/dev/null 2>&1; then
  nft delete table "${TABLE%% *}" "${TABLE##* }"
fi

nft add table ${TABLE}
nft "add chain ${TABLE} ${CHAIN} { type filter hook forward priority 0; policy drop; }"
for port in ${ALLOWED_TCP_PORTS}; do
  nft add rule ${TABLE} ${CHAIN} iifname "${BRIDGE_IF}" ip daddr "${GATEWAY_IP}" tcp dport "${port}" accept
done
nft add rule ${TABLE} ${CHAIN} ct state established,related accept

echo "Applied agent sandbox forward policy for host ports: ${ALLOWED_TCP_PORTS}"
