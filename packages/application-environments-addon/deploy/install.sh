#!/usr/bin/env bash
set -euo pipefail
test "$(id -u)" = 0 || { echo "Run through normal root deployment" >&2; exit 1; }
test -f /etc/evander/application-worker.env
test -f /etc/evander/application-worker.json
source_dir="$(cd "$(dirname "$0")" && pwd)"
install -m 0644 "$source_dir/evander-application-environment-worker.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable evander-application-environment-worker.service
echo "Installed optional application worker. Review private listeners, then start explicitly."
