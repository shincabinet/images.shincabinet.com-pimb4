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

echo
echo "== dynamic image registry =="
if [[ -f /mnt/storage/shincabinet-images/.image-index.json ]]; then
  python3 - <<'PY'
import json
from pathlib import Path
p=Path('/mnt/storage/shincabinet-images/.image-index.json')
data=json.loads(p.read_text())
print(f"registry: {p}")
print(f"image IDs: {len(data.get('images', {}))}")
PY
else
  echo "Registry has not been created yet. Restart the image manager after updating."
fi

echo
echo "== nginx dynamic ID route =="
if grep -q 'SHINCABINET_DYNAMIC_IMAGES_BEGIN' /etc/nginx/sites-enabled/images.shincabinet.com 2>/dev/null || \
   grep -q 'SHINCABINET_DYNAMIC_IMAGES_BEGIN' /etc/nginx/sites-available/images.shincabinet.com 2>/dev/null; then
  echo "dynamic /i/ proxy: configured"
else
  echo "dynamic /i/ proxy: NOT FOUND"
  echo "Run: sudo ./scripts/install-nginx-dynamic-route.sh"
fi
