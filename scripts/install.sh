#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="${SUDO_USER:-$USER}"
APP_GROUP="$(id -gn "$APP_USER")"
ENV_FILE="/etc/shincabinet-image-manager.env"
UNIT_FILE="/etc/systemd/system/shincabinet-image-manager.service"
STORAGE_ROOT="/mnt/storage/shincabinet-images"

if [[ $EUID -ne 0 ]]; then
  echo "Run this installer with sudo: sudo ./scripts/install.sh" >&2
  exit 1
fi

if ! mountpoint -q /mnt/storage; then
  echo "/mnt/storage is not a mounted filesystem. Mount the HDD first." >&2
  exit 1
fi

mkdir -p "$STORAGE_ROOT"/{gallery,characters,adoptables,misc}
chown -R "$APP_USER:$APP_GROUP" "$STORAGE_ROOT"
chmod 0755 "$STORAGE_ROOT"

apt-get update
apt-get install -y python3 python3-venv python3-pip

if [[ ! -d "$APP_DIR/.venv" ]]; then
  sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
fi
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [[ ! -f "$ENV_FILE" ]]; then
  echo
  read -r -s -p "Choose an Image Manager password (12+ characters): " MANAGER_PASSWORD
  echo
  if (( ${#MANAGER_PASSWORD} < 12 )); then
    echo "Password must be at least 12 characters." >&2
    exit 1
  fi
  SECRET="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
  API_TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
  umask 077
  MANAGER_PASSWORD="$MANAGER_PASSWORD" SECRET="$SECRET" API_TOKEN="$API_TOKEN" python3 - "$ENV_FILE" "$STORAGE_ROOT" <<'PY'
import os
import sys
from pathlib import Path

def q(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'

path = Path(sys.argv[1])
storage = sys.argv[2]
values = {
    "SHIN_IMAGE_ROOT": storage,
    "SHIN_STORAGE_MOUNT": "/mnt/storage",
    "SHIN_REQUIRE_STORAGE_MOUNT": "1",
    "SHIN_PUBLIC_BASE_URL": "https://images.shincabinet.com",
    "SHIN_IMAGE_MANAGER_HOST": "127.0.0.1",
    "SHIN_IMAGE_MANAGER_PORT": "8090",
    "SHIN_IMAGE_MANAGER_PASSWORD": os.environ["MANAGER_PASSWORD"],
    "SHIN_IMAGE_MANAGER_SECRET": os.environ["SECRET"],
    "SHIN_IMAGE_MANAGER_API_TOKEN": os.environ["API_TOKEN"],
    "SHIN_IMAGE_MANAGER_MAX_UPLOAD_MB": "512",
    "SHIN_USE_CLOUDFLARE_THUMBNAILS": "1",
    "SHIN_THUMBNAIL_SIZE": "480",
    "SHIN_SECURE_COOKIE": "1",
}
path.write_text("".join(f"{key}={q(value)}\n" for key, value in values.items()))
PY
  chmod 0600 "$ENV_FILE"
else
  echo "Keeping existing $ENV_FILE"
fi

sed \
  -e "s|__USER__|$APP_USER|g" \
  -e "s|__GROUP__|$APP_GROUP|g" \
  -e "s|__APP_DIR__|$APP_DIR|g" \
  "$APP_DIR/systemd/shincabinet-image-manager.service.template" > "$UNIT_FILE"

systemctl daemon-reload
systemctl enable --now shincabinet-image-manager

echo
echo "Installed."
echo "Service: systemctl status shincabinet-image-manager"
echo "Local UI: http://127.0.0.1:8090"
echo "Storage: $STORAGE_ROOT"
