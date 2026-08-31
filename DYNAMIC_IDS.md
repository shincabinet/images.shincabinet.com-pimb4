# Dynamic ID migration

After deploying this version to the Pi:

1. Restart `shincabinet-image-manager` once. It automatically indexes every existing image file and creates `.image-index.json`.
2. Run `sudo ./scripts/install-nginx-dynamic-route.sh` once.
3. Open the manager over Tailscale. Each image card now displays a permanent `img_...` ID.
4. In the primary website Site Manager, replace old `/assets/images/...` or direct `https://images.shincabinet.com/...` references with the matching ID.
5. Once a website slot uses an ID, use **Replace** in this manager whenever the artwork changes. Do not give the replacement a new website assignment.

Use the manager for moves/renames so it can preserve the ID mapping.

Back up `/mnt/storage/shincabinet-images/.image-index.json` together with the originals.
