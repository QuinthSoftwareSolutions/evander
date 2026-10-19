#!/usr/bin/env bash
set -euo pipefail
role="${1:-}"
case "$role" in relay|worker) ;; *) echo "Usage: bash deploy/install.sh relay|worker" >&2; exit 2 ;; esac
test "$(id -u)" = 0 || { echo "Run as root through normal deployment" >&2; exit 1; }
source_dir="$(cd "$(dirname "$0")" && pwd)"
unit="evander-browser-session-${role}.service"
test -f "/etc/evander/browser-${role}.env"
if test "$role" = worker; then test -f /etc/evander/browser-worker.json; fi
install -m 0644 "$source_dir/$unit" "/etc/systemd/system/$unit"
systemctl daemon-reload
systemctl enable "$unit"
echo "Installed $unit. Review paths/private listeners, then start explicitly."
