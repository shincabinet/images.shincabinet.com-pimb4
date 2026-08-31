# API notes — Dynamic Image IDs

The browser UI is the primary administration interface. An optional bearer token can be used by trusted local tools. Never commit the token to either Git repository.

Authenticated example:

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  "http://127.0.0.1:8090/api/list?path=gallery"
```

## Public read endpoint

```text
GET /i/<image-id>
GET /i/<image-id>?max=2048
```

This endpoint does not require manager authentication. It is intended to be proxied by Nginx at `images.shincabinet.com/i/...`.

Response headers include:

```text
X-Shin-Image-Id
X-Shin-Image-Revision
ETag
```

## Authenticated endpoints

```text
GET    /api/config
GET    /api/list?path=gallery
GET    /api/image/<image-id>
POST   /api/folders
POST   /api/upload
POST   /api/image/<image-id>/replace
POST   /api/move
DELETE /api/item
```

`/api/list` image objects now include:

```json
{
  "id": "img_9f73c215bf6a46b78973b6317dc16c3a",
  "path": "gallery/example.webp",
  "url": "https://images.shincabinet.com/i/img_9f73c215bf6a46b78973b6317dc16c3a",
  "directUrl": "https://images.shincabinet.com/gallery/example.webp",
  "thumbnailUrl": "/i/img_9f73c215bf6a46b78973b6317dc16c3a?max=480"
}
```

The `id` is the value that should be stored in the primary website configuration.

## Replacement semantics

`POST /api/image/<image-id>/replace` accepts multipart field `file`.

The backing filename may change, including its extension. The Image ID is preserved and its revision is incremented. Old generated derivatives are invalidated automatically.
