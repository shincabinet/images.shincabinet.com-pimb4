#!/usr/bin/env bash
set -euo pipefail

echo "== service =="
systemctl --no-pager --full status shincabinet-image-manager || true

echo
echo "== listener =="
ss -ltnp | grep ':8090' || true

echo
echo "== local health =="
curl -fsS http://127.0.0.1:8090/health && echo

echo
echo "== storage =="
findmnt /mnt/storage || true
df -h /mnt/storage || true
