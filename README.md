# ShinCabinet Image Manager

A small private web application for managing the artwork served by `https://images.shincabinet.com`.

**The image files are never stored in this Git repository.** They live directly on the Raspberry Pi at:

```text
/mnt/storage/shincabinet-images/
```

Nginx continues to serve that directory publicly through the existing Cloudflare Tunnel at `images.shincabinet.com`. This manager is a separate administrative application, normally exposed at something like `manage-images.shincabinet.com` through the same Cloudflare Tunnel.

## What it does

- Browse image folders on the 5 TB HDD.
- Upload one or many images directly to the HDD.
- Create folders.
- Rename or move images/folders.
- Delete images and empty folders.
- Display dimensions and file size when available.
- Copy the permanent public URL for an image with one click.
- Open the untouched original image.
- Use Cloudflare transformed thumbnails in the manager UI so browsing does not repeatedly download huge originals.
- Password-protected session login.
- Optional bearer-token API for future Site Manager integration.
- Refuses hidden paths, `..` traversal, symlinks, and non-image uploads.

The filesystem is the source of truth. There is no image database to lose or synchronize.

## Intended architecture

```text
Your desktop browser
        |
        | HTTPS
        v
manage-images.shincabinet.com
        |
        v
Cloudflare Tunnel
        |
        v
127.0.0.1:8090
ShinCabinet Image Manager
        |
        v
/mnt/storage/shincabinet-images
        |
        +-----------------------------+
                                      |
                                      v
                         Nginx 127.0.0.1:8080
                                      |
                                      v
                         images.shincabinet.com
```

Your website repository stores only URLs/metadata. Example:

```javascript
image: "https://images.shincabinet.com/gallery/shinji/lounge.webp"
```

## Suggested storage structure

```text
/mnt/storage/shincabinet-images/
├── gallery/
│   ├── shinji-lounge/
│   │   ├── primary.webp
│   │   └── alt-01.webp
│   └── bird-oc/
│       └── primary.png
├── characters/
│   ├── shinji/
│   │   ├── reference/
│   │   └── gallery/
│   └── kite/
│       ├── reference/
│       └── gallery/
├── adoptables/
└── misc/
```

## Raspberry Pi installation

Clone this repository somewhere that will remain on the Pi. `/opt` is a good choice:

```bash
cd /opt
sudo git clone YOUR_REPOSITORY_URL shincabinet-image-manager
sudo chown -R "$USER:$USER" /opt/shincabinet-image-manager
cd /opt/shincabinet-image-manager
sudo ./scripts/install.sh
```

The installer:

1. Creates `/mnt/storage/shincabinet-images` if needed.
2. Creates a Python virtual environment.
3. Installs Flask, Gunicorn and Pillow.
4. Prompts for an admin password.
5. Creates a random Flask session secret.
6. Writes secrets to `/etc/shincabinet-image-manager.env`, not Git.
7. Installs and starts a systemd service.
8. Binds the application to `127.0.0.1:8090` only.
9. Refuses to run if `/mnt/storage` is not actually mounted, preventing accidental microSD uploads.

Verify:

```bash
sudo ./scripts/check.sh
```

Or individually:

```bash
systemctl status shincabinet-image-manager
curl http://127.0.0.1:8090/health
ss -ltnp | grep 8090
```

Expected health response:

```json
{"ok":true}
```

## Add the private manager hostname to Cloudflare Tunnel

In the Cloudflare Tunnel already running on the Pi, add another published application route:

```text
Hostname: manage-images.shincabinet.com
Service:  HTTP
URL:      localhost:8090
```

Do **not** point the management application at port `8080`; that is the public image origin.

For the management hostname, use Cloudflare Access in front of the application if possible. The application also has its own password, so Access gives you a second authentication layer.

The public hostname remains:

```text
images.shincabinet.com -> http://localhost:8080
```

and the private administration hostname becomes:

```text
manage-images.shincabinet.com -> http://localhost:8090
```

## Configuration

Runtime configuration is stored in:

```text
/etc/shincabinet-image-manager.env
```

Example:

```dotenv
SHIN_IMAGE_ROOT=/mnt/storage/shincabinet-images
SHIN_STORAGE_MOUNT=/mnt/storage
SHIN_REQUIRE_STORAGE_MOUNT=1
SHIN_PUBLIC_BASE_URL=https://images.shincabinet.com
SHIN_IMAGE_MANAGER_HOST=127.0.0.1
SHIN_IMAGE_MANAGER_PORT=8090
SHIN_IMAGE_MANAGER_PASSWORD=your-long-password
SHIN_IMAGE_MANAGER_SECRET=a-long-random-secret
SHIN_IMAGE_MANAGER_API_TOKEN=a-random-token-generated-by-the-installer
SHIN_IMAGE_MANAGER_MAX_UPLOAD_MB=512
SHIN_USE_CLOUDFLARE_THUMBNAILS=1
SHIN_THUMBNAIL_SIZE=480
SHIN_SECURE_COOKIE=1
```

After changing it:

```bash
sudo systemctl restart shincabinet-image-manager
```

See `API.md` for the optional API intended for future direct Site Manager integration.

## Normal workflow

1. Open `https://manage-images.shincabinet.com`.
2. Navigate/create the appropriate folder.
3. Upload the original artwork.
4. Click **Copy URL**.
5. Paste that URL into the appropriate Gallery/Character/Adoptable field in the `shincabinet.com` Site Manager.
6. Save/push the website metadata.

The image itself never enters the website Git repository.

## Original vs web-sized versions

The Image Manager does not modify originals. An uploaded `6000x4000` image stays `6000x4000` on the HDD and at its original URL.

Your public website can request a capped version through Cloudflare Image Transformations, for example:

```text
https://images.shincabinet.com/cdn-cgi/image/fit=scale-down,width=2048,height=2048,format=auto/gallery/example.png
```

while the original remains:

```text
https://images.shincabinet.com/gallery/example.png
```

This repository uses the same approach for its own browsing thumbnails when `SHIN_USE_CLOUDFLARE_THUMBNAILS=1`.

## Updating the manager

```bash
cd /opt/shincabinet-image-manager
git pull
./.venv/bin/pip install -r requirements.txt
sudo systemctl restart shincabinet-image-manager
```

## Backups

This manager intentionally does not treat GitHub as an image backup. Back up `/mnt/storage/shincabinet-images` separately. At minimum, maintain a second copy on another disk/system before treating the Pi as the only source of original artwork.
