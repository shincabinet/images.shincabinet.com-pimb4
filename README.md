# ShinCabinet Image Manager — Dynamic Image IDs

Private Raspberry Pi image-management application for `https://images.shincabinet.com`.

Artwork is **not stored in Git**. Originals live at:

```text
/mnt/storage/shincabinet-images/
```

The important difference in this version is that website content no longer needs to know the file path. Every image gets a permanent ID such as:

```text
img_9f73c215bf6a46b78973b6317dc16c3a
```

The public stable URL is:

```text
https://images.shincabinet.com/i/img_9f73c215bf6a46b78973b6317dc16c3a
```

The website should store the **ID**, not that URL. The website runtime constructs the URL from the configured image host.

## Why IDs

An ID points to the current backing file through `/mnt/storage/shincabinet-images/.image-index.json`.

```text
Website slot
  image = img_9f73...
          |
          v
images.shincabinet.com/i/img_9f73...
          |
          v
Image Manager registry
          |
          +--> gallery/shinji/old-name.png
```

If that image is replaced with `gallery/shinji/new-render.webp`, the ID remains unchanged. Every character, gallery entry, alternative image, commission example, etc. using that ID changes automatically.

Moving/renaming a managed file also preserves its ID.

## Dynamic web-sized delivery

The stable route supports a `max` query parameter:

```text
https://images.shincabinet.com/i/img_9f73...?max=2048
```

The original remains untouched on disk. The manager creates a cached derivative in its hidden `.dynamic-cache` directory only when the original is larger than the requested bound. Aspect ratio is preserved and smaller images are never upscaled.

When an image is replaced under the same ID:

1. the ID revision increases;
2. cached derivatives for that ID are removed;
3. the next request rebuilds the derivative from the new original.

This avoids stale Cloudflare Image Transformation results for stable IDs. Legacy/direct URL images can still use Cloudflare transformations during migration.

## Files created on the HDD

```text
/mnt/storage/shincabinet-images/
├── .image-index.json       # CRITICAL: ID -> file mapping
├── .image-index.lock
├── .dynamic-cache/         # disposable generated derivatives
├── gallery/
├── characters/
├── adoptables/
└── misc/
```

**Back up `.image-index.json` with your artwork.** The IDs in this file are what the primary website stores. Losing it and regenerating IDs would require relinking the website.

`.dynamic-cache/` does not need to be backed up.

## Manager features

- Browse folders on the 5 TB HDD.
- Upload multiple images.
- Automatically assign an ID to every new image.
- Automatically index pre-existing image files.
- Copy an image ID.
- Copy the stable `/i/<id>` URL.
- Replace an image while preserving its ID.
- Rename/move files while preserving IDs.
- Delete images and remove their IDs.
- Serve web-sized derivatives with `?max=`.
- Display image dimensions and file sizes.
- Password-protected browser UI.
- Optional bearer-token API.
- Refuses to start when `/mnt/storage` is not actually mounted.

## Architecture

```text
PRIVATE MANAGEMENT
Desktop -> Tailscale -> Image Manager :8090
                         |
                         v
                /mnt/storage/shincabinet-images

PUBLIC IMAGE DELIVERY
Browser -> Cloudflare Tunnel -> Nginx :8080
                                  |
                  /normal/path    |    /i/img_...
                     |            |       |
                     v            |       v
                 static HDD       |   proxy :8090
                                  |       |
                                  |       v
                                  |   ID lookup + optional resize
```

The manager UI can remain Tailscale-only. Only `/i/` is proxied through the public Nginx image origin.

## Install/update on the Pi

For the dedicated Pi repo, a typical live checkout is:

```text
/opt/images.shincabinet.com-pimb4
```

Initial installation:

```bash
cd /opt/images.shincabinet.com-pimb4
sudo ./scripts/install.sh
```

Then install the one-time Nginx dynamic-ID route:

```bash
sudo ./scripts/install-nginx-dynamic-route.sh
```

That adds a public read-only proxy for `/i/` to the already-local manager on `127.0.0.1:8090`, validates Nginx, makes a backup, and reloads Nginx.

Verify:

```bash
sudo nginx -t
sudo systemctl status shincabinet-image-manager --no-pager
curl -I http://127.0.0.1:8090/health
```

After uploading an image, copy its ID from the manager and test:

```bash
curl -I https://images.shincabinet.com/i/IMAGE_ID
curl -I 'https://images.shincabinet.com/i/IMAGE_ID?max=512'
```

Both should return `200` for a valid ID.

## Cloudflare Tunnel

No new Cloudflare hostname is required for dynamic IDs. Keep the existing public route:

```text
images.shincabinet.com -> http://localhost:8080
```

Nginx decides whether the request is a normal static path or `/i/<id>`.

The administrative Image Manager can continue to be reached over Tailscale Serve; it does not need a public Cloudflare hostname.

## Normal workflow

1. Open the Raspberry Pi Image Manager over Tailscale.
2. Upload an original.
3. Copy its `img_...` ID.
4. Open the primary `shincabinet.com` Site Manager.
5. Paste the ID into a character/gallery/reference/etc. image assignment.
6. Commit/push only website metadata.
7. Later, use **Replace** in the Pi manager to swap the backing image without changing the website.

## Security

The `/i/<id>` route is intentionally unauthenticated because it is the public artwork delivery route. The management UI and write APIs remain authenticated. Direct static files are still subject to your existing Nginx rules.

## Website Site Manager integration

The primary `shincabinet.com` Site Manager can connect to this service over Tailscale using the API token. It uses `/api/catalog` to browse permanent `img_...` IDs, `/api/lookup` to migrate legacy paths, and `/api/image/<id>/replace` to replace a backing file while preserving the ID.

The public website still loads images through `/i/<img_id>` via the existing Nginx/Cloudflare Tunnel route.
