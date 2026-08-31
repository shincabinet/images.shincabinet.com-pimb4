#!/usr/bin/env bash
set -euo pipefail

SITE_FILE="${1:-/etc/nginx/sites-available/images.shincabinet.com}"
MARKER_BEGIN="# SHINCABINET_DYNAMIC_IMAGES_BEGIN"
MARKER_END="# SHINCABINET_DYNAMIC_IMAGES_END"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo ./scripts/install-nginx-dynamic-route.sh" >&2
  exit 1
fi
if [[ ! -f "$SITE_FILE" ]]; then
  echo "Nginx site file not found: $SITE_FILE" >&2
  exit 1
fi

BACKUP="${SITE_FILE}.before-dynamic-images.$(date +%Y%m%d%H%M%S)"

# Upgrade an existing /i/ installation by adding /s/ without duplicating the block.
if grep -qF "$MARKER_BEGIN" "$SITE_FILE"; then
  if grep -q 'location \^~ /s/' "$SITE_FILE"; then
    echo "Dynamic /i/ and /s/ routes are already installed in $SITE_FILE"
    nginx -t
    exit 0
  fi

  cp -a "$SITE_FILE" "$BACKUP"
  python3 - "$SITE_FILE" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
marker = "    # SHINCABINET_DYNAMIC_IMAGES_END"
block = '''    # Website-owned stable aliases (siteimg_...) are resolved by the same manager.
    location ^~ /s/ {
        proxy_pass http://127.0.0.1:8090;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
'''
if marker not in s:
    raise SystemExit("Dynamic image marker end was not found.")
p.write_text(s.replace(marker, block + marker, 1))
PY

  if ! nginx -t; then
    cp -a "$BACKUP" "$SITE_FILE"
    echo "Nginx validation failed; restored $BACKUP" >&2
    exit 1
  fi
  systemctl reload nginx
  echo "Added /s/<siteimg-id> to the existing dynamic image routes."
  echo "Backup: $BACKUP"
  exit 0
fi

cp -a "$SITE_FILE" "$BACKUP"
python3 - "$SITE_FILE" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
needle = "    location / {"
block = '''    # SHINCABINET_DYNAMIC_IMAGES_BEGIN
    # Stable image IDs are resolved by the local image manager. Only these
    # public read-only routes are proxied; the manager UI remains localhost/Tailscale-only.
    location ^~ /i/ {
        proxy_pass http://127.0.0.1:8090;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
    # Website-owned stable aliases (siteimg_...) are resolved by the same manager.
    location ^~ /s/ {
        proxy_pass http://127.0.0.1:8090;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
    # SHINCABINET_DYNAMIC_IMAGES_END

'''
if needle not in s:
    raise SystemExit("Could not find the existing `    location / {` block.")
p.write_text(s.replace(needle, block + needle, 1))
PY

if ! nginx -t; then
  cp -a "$BACKUP" "$SITE_FILE"
  echo "Nginx validation failed; restored $BACKUP" >&2
  exit 1
fi
systemctl reload nginx
echo "Installed /i/<image-id> and /s/<siteimg-id> proxy routes and reloaded Nginx."
echo "Backup: $BACKUP"
