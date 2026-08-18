#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: apply_firewall.sh [--help]

Apply the nftables forward policy for the Evander network.

Environment:
  ALLOWED_TCP_PORTS  Space-separated host TCP ports reachable from guests.
                     Default: "8000 11434"
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

TABLE="inet evander"
INPUT_CHAIN="input"
FORWARD_CHAIN="forward"
BRIDGE_IF="virbr250"
GATEWAY_IP="192.168.250.1"
ALLOWED_TCP_PORTS="${ALLOWED_TCP_PORTS:-22 8000 11434 11666}"

if ! command -v nft >/dev/null 2>&1; then
  echo "nft is required" >&2
  exit 1
fi

delete_table_if_exists() {
  local table="$1"
  local family="${table%% *}"
  local name="${table##* }"
  if nft list table "${family}" "${name}" >/dev/null 2>&1; then
    nft delete table "${family}" "${name}"
  fi
}

delete_table_if_exists "${TABLE}"

nft add table ${TABLE}
nft "add chain ${TABLE} ${INPUT_CHAIN} { type filter hook input priority 0; policy accept; }"
nft "add chain ${TABLE} ${FORWARD_CHAIN} { type filter hook forward priority 0; policy drop; }"
for port in ${ALLOWED_TCP_PORTS}; do
  nft add rule ${TABLE} ${INPUT_CHAIN} iifname "${BRIDGE_IF}" ip daddr "${GATEWAY_IP}" tcp dport "${port}" accept
  nft add rule ${TABLE} ${FORWARD_CHAIN} iifname "${BRIDGE_IF}" ip daddr "${GATEWAY_IP}" tcp dport "${port}" accept
done
nft add rule ${TABLE} ${FORWARD_CHAIN} ct state established,related accept

echo "Applied Evander forward policy for host ports: ${ALLOWED_TCP_PORTS}"
