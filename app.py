from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import shutil
import time
import uuid
import fcntl
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

try:
    from PIL import Image, ImageOps
except Exception:  # Pillow is optional at runtime for dimensions only.
    Image = None
    ImageOps = None


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp", ".tif", ".tiff"
}


@dataclass(frozen=True)
class Settings:
    storage_root: Path
    public_base_url: str
    bind_host: str
    bind_port: int
    admin_password: str
    secret_key: str
    max_upload_mb: int
    transform_thumbnails: bool
    thumbnail_size: int
    api_token: str


def load_settings() -> Settings:
    storage_root = Path(os.getenv("SHIN_IMAGE_ROOT", "/mnt/storage/shincabinet-images")).expanduser().resolve()
    mount_point = Path(os.getenv("SHIN_STORAGE_MOUNT", "/mnt/storage")).expanduser().resolve()
    require_mount = os.getenv("SHIN_REQUIRE_STORAGE_MOUNT", "1").lower() not in {"0", "false", "no"}
    if require_mount and not mount_point.is_mount():
        raise RuntimeError(f"Required storage mount is not mounted: {mount_point}")
    try:
        storage_root.relative_to(mount_point)
    except ValueError as exc:
        raise RuntimeError(f"SHIN_IMAGE_ROOT must be inside {mount_point} when mount protection is enabled.") from exc
    public_base_url = os.getenv("SHIN_PUBLIC_BASE_URL", "https://images.shincabinet.com").rstrip("/")
    admin_password = os.getenv("SHIN_IMAGE_MANAGER_PASSWORD", "")
    secret_key = os.getenv("SHIN_IMAGE_MANAGER_SECRET", "")

    if not admin_password:
        raise RuntimeError("SHIN_IMAGE_MANAGER_PASSWORD must be set.")
    if len(admin_password) < 12:
        raise RuntimeError("SHIN_IMAGE_MANAGER_PASSWORD must be at least 12 characters.")
    if not secret_key or len(secret_key) < 32:
        raise RuntimeError("SHIN_IMAGE_MANAGER_SECRET must be set to a random string of at least 32 characters.")

    return Settings(
        storage_root=storage_root,
        public_base_url=public_base_url,
        bind_host=os.getenv("SHIN_IMAGE_MANAGER_HOST", "127.0.0.1"),
        bind_port=int(os.getenv("SHIN_IMAGE_MANAGER_PORT", "8090")),
        admin_password=admin_password,
        secret_key=secret_key,
        max_upload_mb=int(os.getenv("SHIN_IMAGE_MANAGER_MAX_UPLOAD_MB", "512")),
        transform_thumbnails=os.getenv("SHIN_USE_CLOUDFLARE_THUMBNAILS", "1").lower() not in {"0", "false", "no"},
        thumbnail_size=int(os.getenv("SHIN_THUMBNAIL_SIZE", "480")),
        api_token=os.getenv("SHIN_IMAGE_MANAGER_API_TOKEN", "").strip(),
    )


settings = load_settings()
settings.storage_root.mkdir(parents=True, exist_ok=True)

IMAGE_ID_RE = re.compile(r"^img_[0-9a-f]{32}$")
SITE_IMAGE_ID_RE = re.compile(r"^siteimg_[0-9a-f]{32}$")
REGISTRY_FILE = settings.storage_root / ".image-index.json"
REGISTRY_BACKUP_FILE = settings.storage_root / ".image-index.json.bak"
REGISTRY_LOCK_FILE = settings.storage_root / ".image-index.lock"
DERIVATIVE_ROOT = settings.storage_root / ".dynamic-cache"
DERIVATIVE_ROOT.mkdir(parents=True, exist_ok=True)


@contextmanager
def registry_lock(*, exclusive: bool):
    REGISTRY_LOCK_FILE.touch(exist_ok=True)
    with REGISTRY_LOCK_FILE.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def empty_registry() -> dict:
    return {"version": 2, "images": {}, "aliases": {}}


def load_registry_unlocked() -> dict:
    if not REGISTRY_FILE.exists():
        return empty_registry()
    try:
        payload = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_registry()
    if not isinstance(payload, dict) or not isinstance(payload.get("images"), dict):
        return empty_registry()
    if not isinstance(payload.get("aliases"), dict):
        payload["aliases"] = {}
    payload["version"] = max(2, int(payload.get("version") or 1))
    return payload


def save_registry_unlocked(payload: dict) -> None:
    temporary = REGISTRY_FILE.with_name(f".{REGISTRY_FILE.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if REGISTRY_FILE.exists():
        try:
            shutil.copy2(REGISTRY_FILE, REGISTRY_BACKUP_FILE)
        except OSError:
            pass
    os.replace(temporary, REGISTRY_FILE)


def valid_image_id(value: str | None) -> str:
    image_id = str(value or "").strip().lower()
    if not IMAGE_ID_RE.fullmatch(image_id):
        raise ValueError("Invalid image ID.")
    return image_id


def valid_site_image_id(value: str | None) -> str:
    site_image_id = str(value or "").strip().lower()
    if not SITE_IMAGE_ID_RE.fullmatch(site_image_id):
        raise ValueError("Invalid website image ID.")
    return site_image_id


def new_image_id(registry: dict) -> str:
    while True:
        image_id = f"img_{uuid.uuid4().hex}"
        if image_id not in registry["images"]:
            return image_id


def id_url(image_id: str) -> str:
    return f"{settings.public_base_url}/i/{image_id}"


def site_alias_url(site_image_id: str) -> str:
    return f"{settings.public_base_url}/s/{site_image_id}"


def alias_image_id(site_image_id: str) -> str | None:
    site_image_id = valid_site_image_id(site_image_id)
    with registry_lock(exclusive=False):
        registry = load_registry_unlocked()
        image_id = registry.get("aliases", {}).get(site_image_id)
    return valid_image_id(image_id) if image_id else None


def set_alias(site_image_id: str, image_id: str) -> dict:
    site_image_id = valid_site_image_id(site_image_id)
    image_id = valid_image_id(image_id)
    with registry_lock(exclusive=True):
        registry = load_registry_unlocked()
        if image_id not in registry["images"]:
            raise ValueError("Unknown Raspberry Pi image ID.")
        aliases = registry.setdefault("aliases", {})
        aliases[site_image_id] = image_id
        save_registry_unlocked(registry)
        entry = dict(registry["images"][image_id])
    return {"siteImageId": site_image_id, "imageId": image_id, "url": site_alias_url(site_image_id), "revision": int(entry.get("revision") or 1)}


def delete_alias(site_image_id: str) -> bool:
    site_image_id = valid_site_image_id(site_image_id)
    with registry_lock(exclusive=True):
        registry = load_registry_unlocked()
        aliases = registry.setdefault("aliases", {})
        existed = site_image_id in aliases
        aliases.pop(site_image_id, None)
        if existed:
            save_registry_unlocked(registry)
    return existed


def cache_path_for(image_id: str, revision: int, max_dimension: int, source: Path) -> Path:
    suffix = source.suffix.lower()
    return DERIVATIVE_ROOT / image_id / f"r{revision}-max{max_dimension}{suffix}"


def clear_derivatives(image_id: str) -> None:
    shutil.rmtree(DERIVATIVE_ROOT / image_id, ignore_errors=True)


def find_id_for_path_unlocked(registry: dict, relative: str) -> str | None:
    for image_id, entry in registry["images"].items():
        if entry.get("path") == relative:
            return image_id
    return None


def register_path(relative: PurePosixPath) -> tuple[str, dict]:
    relative_text = relative_string(relative)
    now = int(time.time())
    with registry_lock(exclusive=True):
        registry = load_registry_unlocked()
        image_id = find_id_for_path_unlocked(registry, relative_text)
        if image_id:
            return image_id, dict(registry["images"][image_id])
        image_id = new_image_id(registry)
        entry = {"path": relative_text, "revision": 1, "createdAt": now, "updatedAt": now}
        registry["images"][image_id] = entry
        save_registry_unlocked(registry)
        return image_id, dict(entry)


def registry_entry(image_id: str) -> dict | None:
    image_id = valid_image_id(image_id)
    with registry_lock(exclusive=False):
        registry = load_registry_unlocked()
        entry = registry["images"].get(image_id)
        return dict(entry) if isinstance(entry, dict) else None


def update_registry_for_path(relative: PurePosixPath, *, content_changed: bool = False) -> tuple[str, dict]:
    relative_text = relative_string(relative)
    now = int(time.time())
    with registry_lock(exclusive=True):
        registry = load_registry_unlocked()
        image_id = find_id_for_path_unlocked(registry, relative_text)
        if image_id is None:
            image_id = new_image_id(registry)
            entry = {"path": relative_text, "revision": 1, "createdAt": now, "updatedAt": now}
            registry["images"][image_id] = entry
        else:
            entry = registry["images"][image_id]
            entry["updatedAt"] = now
            if content_changed:
                entry["revision"] = int(entry.get("revision") or 1) + 1
        save_registry_unlocked(registry)
        if content_changed:
            clear_derivatives(image_id)
        return image_id, dict(entry)


def reconcile_registry() -> None:
    paths: set[str] = set()
    for child in settings.storage_root.rglob("*"):
        try:
            rel = child.relative_to(settings.storage_root)
        except ValueError:
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue
        if is_image_file(child) and not child.is_symlink():
            paths.add(PurePosixPath(*rel.parts).as_posix())

    now = int(time.time())
    with registry_lock(exclusive=True):
        registry = load_registry_unlocked()
        images = registry["images"]
        changed = False
        for image_id in list(images):
            entry = images[image_id]
            if not isinstance(entry, dict) or entry.get("path") not in paths:
                images.pop(image_id, None)
                clear_derivatives(image_id)
                changed = True
        indexed = {entry.get("path") for entry in images.values() if isinstance(entry, dict)}
        for relative_text in sorted(paths - indexed):
            image_id = new_image_id(registry)
            images[image_id] = {"path": relative_text, "revision": 1, "createdAt": now, "updatedAt": now}
            changed = True
        if changed or not REGISTRY_FILE.exists():
            save_registry_unlocked(registry)


def save_uploaded_file(item, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{secrets.token_hex(6)}.upload")
    try:
        item.save(tmp)
        if Image is not None:
            try:
                with Image.open(tmp) as image:
                    image.verify()
            except Exception as exc:
                raise ValueError(f"Image validation failed: {exc}") from exc
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def resized_path(image_id: str, entry: dict, source: Path, max_dimension: int) -> Path:
    revision = int(entry.get("revision") or 1)
    if Image is None or max_dimension <= 0 or source.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        return source
    try:
        with Image.open(source) as probe:
            if probe.width <= max_dimension and probe.height <= max_dimension:
                return source
    except Exception:
        return source

    target = cache_path_for(image_id, revision, max_dimension, source)
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened) if ImageOps is not None else opened.copy()
            image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            suffix = source.suffix.lower()
            if suffix in {".jpg", ".jpeg"}:
                if image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                image.save(temporary, format="JPEG", quality=90, optimize=True)
            elif suffix == ".webp":
                image.save(temporary, format="WEBP", quality=90, method=4)
            else:
                image.save(temporary, format="PNG", optimize=True)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


app = Flask(__name__)
app.secret_key = settings.secret_key
app.config.update(
    MAX_CONTENT_LENGTH=settings.max_upload_mb * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SHIN_SECURE_COOKIE", "1").lower() not in {"0", "false", "no"},
)


def normalize_relative(raw: str | None, *, allow_empty: bool = True) -> PurePosixPath:
    raw = (raw or "").strip().replace("\\", "/")
    if raw in {"", "."}:
        if allow_empty:
            return PurePosixPath()
        raise ValueError("A path is required.")

    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"..", ""} for part in path.parts):
        raise ValueError("Invalid path.")
    if any(part.startswith(".") for part in path.parts):
        raise ValueError("Hidden paths are not allowed.")
    return path


def disk_path(relative: PurePosixPath) -> Path:
    candidate = (settings.storage_root / Path(*relative.parts)).resolve(strict=False)
    root = settings.storage_root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Path escapes storage root.") from exc
    return candidate


def relative_string(path: PurePosixPath) -> str:
    return path.as_posix() if path.parts else ""


def encoded_public_path(relative: PurePosixPath) -> str:
    return "/".join(quote(part, safe="") for part in relative.parts)


def public_url(relative: PurePosixPath) -> str:
    encoded = encoded_public_path(relative)
    return f"{settings.public_base_url}/{encoded}" if encoded else settings.public_base_url + "/"


def thumbnail_url(relative: PurePosixPath) -> str:
    if not settings.transform_thumbnails:
        return public_url(relative)
    encoded = encoded_public_path(relative)
    size = settings.thumbnail_size
    return (
        f"{settings.public_base_url}/cdn-cgi/image/"
        f"fit=scale-down,width={size},height={size},format=auto,onerror=redirect/{encoded}"
    )


def is_image_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS



def catalog_items() -> list[dict]:
    """Return a flat, authenticated catalog for other management tools.

    The public website never needs this API; it is intended for the local
    shincabinet.com Site Manager over Tailscale. Stable image IDs remain the
    contract while paths may change.
    """
    with registry_lock(exclusive=False):
        registry = load_registry_unlocked()
        entries = [(image_id, dict(entry)) for image_id, entry in registry["images"].items() if isinstance(entry, dict)]

    result: list[dict] = []
    for image_id, entry in entries:
        try:
            relative = normalize_relative(str(entry.get("path") or ""), allow_empty=False)
            target = disk_path(relative)
        except ValueError:
            continue
        exists = target.exists() and is_image_file(target)
        width, height = image_dimensions(target) if exists else (None, None)
        stat = target.stat() if exists else None
        result.append({
            "id": image_id,
            "path": relative_string(relative),
            "name": target.name,
            "url": id_url(image_id),
            "directUrl": public_url(relative),
            "thumbnailUrl": f"{settings.public_base_url}/i/{image_id}?max={settings.thumbnail_size}",
            "revision": int(entry.get("revision") or 1),
            "updatedAt": entry.get("updatedAt"),
            "createdAt": entry.get("createdAt"),
            "exists": exists,
            "width": width,
            "height": height,
            "size": stat.st_size if stat else 0,
            "modified": int(stat.st_mtime) if stat else None,
        })
    result.sort(key=lambda item: (str(item.get("path") or "").lower(), item["id"]))
    return result

def image_dimensions(path: Path) -> tuple[int | None, int | None]:
    if Image is None:
        return None, None
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:
        return None, None


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def require_csrf() -> None:
    if valid_api_token():
        return
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
    expected = session.get("csrf_token") or ""
    if not supplied or not expected or not hmac.compare_digest(supplied, expected):
        raise PermissionError("Invalid CSRF token.")


def logged_in() -> bool:
    return session.get("authenticated") is True


def valid_api_token() -> bool:
    if not settings.api_token:
        return False
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    supplied = auth[7:].strip()
    return bool(supplied) and hmac.compare_digest(supplied, settings.api_token)


@app.before_request
def protect_routes():
    if request.path.startswith("/static/") or request.path.startswith("/i/") or request.path.startswith("/s/") or request.endpoint in {"login", "health"}:
        return None
    if not logged_in() and not (request.path.startswith("/api/") and valid_api_token()):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Authentication required."}), 401
        return redirect(url_for("login"))
    return None


@app.errorhandler(RequestEntityTooLarge)
def too_large(_error):
    return jsonify({"error": f"Upload exceeds the {settings.max_upload_mb} MB request limit."}), 413


@app.errorhandler(ValueError)
def value_error(error):
    return jsonify({"error": str(error)}), 400


@app.errorhandler(PermissionError)
def permission_error(error):
    return jsonify({"error": str(error)}), 403


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        supplied = request.form.get("password", "")
        if hmac.compare_digest(supplied, settings.admin_password):
            session.clear()
            session["authenticated"] = True
            csrf_token()
            return redirect(url_for("index"))
        error = "Incorrect password."
    return render_template("login.html", error=error)


@app.post("/logout")
def logout():
    require_csrf()
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
def index():
    return render_template(
        "index.html",
        csrf_token=csrf_token(),
        public_base_url=settings.public_base_url,
        max_upload_mb=settings.max_upload_mb,
    )


@app.get("/api/config")
def api_config():
    usage = shutil.disk_usage(settings.storage_root)
    return jsonify({
        "storageRoot": str(settings.storage_root),
        "publicBaseUrl": settings.public_base_url,
        "maxUploadMb": settings.max_upload_mb,
        "thumbnailSize": settings.thumbnail_size,
        "cloudflareThumbnails": settings.transform_thumbnails,
        "dynamicIds": True,
        "dynamicBaseUrl": f"{settings.public_base_url}/i/",
        "siteAliasBaseUrl": f"{settings.public_base_url}/s/",
        "siteAliases": True,
        "disk": {"total": usage.total, "used": usage.used, "free": usage.free},
    })


@app.get("/api/list")
def api_list():
    relative = normalize_relative(request.args.get("path"))
    base = disk_path(relative)
    if not base.exists():
        return jsonify({"error": "Folder does not exist."}), 404
    if not base.is_dir():
        return jsonify({"error": "Path is not a folder."}), 400

    items = []
    for child in sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name.startswith(".") or child.is_symlink():
            continue
        child_rel = relative / child.name
        stat = child.stat()
        if child.is_dir():
            items.append({
                "type": "folder",
                "name": child.name,
                "path": relative_string(child_rel),
                "modified": int(stat.st_mtime),
            })
        elif is_image_file(child):
            width, height = image_dimensions(child)
            image_id, _entry = register_path(child_rel)
            items.append({
                "type": "image",
                "name": child.name,
                "path": relative_string(child_rel),
                "size": stat.st_size,
                "modified": int(stat.st_mtime),
                "width": width,
                "height": height,
                "id": image_id,
                "url": id_url(image_id),
                "directUrl": public_url(child_rel),
                "thumbnailUrl": f"/i/{image_id}?max={settings.thumbnail_size}",
            })

    parent = relative.parent if relative.parts else PurePosixPath()
    return jsonify({
        "path": relative_string(relative),
        "parent": relative_string(parent),
        "items": items,
    })



@app.get("/api/catalog")
def api_catalog():
    query = str(request.args.get("q") or "").strip().lower()
    items = catalog_items()
    if query:
        items = [item for item in items if query in f"{item['id']} {item['path']} {item['name']}".lower()]
    return jsonify({"items": items, "count": len(items)})


@app.get("/api/lookup")
def api_lookup():
    raw = str(request.args.get("path") or "").strip()
    if raw.startswith("/assets/images/"):
        raw = raw[len("/assets/images/"):]
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    relative = normalize_relative(raw, allow_empty=False)
    relative_text = relative_string(relative)
    with registry_lock(exclusive=False):
        registry = load_registry_unlocked()
        image_id = find_id_for_path_unlocked(registry, relative_text)
        entry = dict(registry["images"][image_id]) if image_id else None
    if not image_id or entry is None:
        return jsonify({"error": "No image ID is registered for that path."}), 404
    target = disk_path(relative)
    width, height = image_dimensions(target) if target.exists() else (None, None)
    return jsonify({
        "id": image_id,
        "path": relative_text,
        "url": id_url(image_id),
        "directUrl": public_url(relative),
        "thumbnailUrl": f"{settings.public_base_url}/i/{image_id}?max={settings.thumbnail_size}",
        "revision": int(entry.get("revision") or 1),
        "exists": target.exists(),
        "width": width,
        "height": height,
        "size": target.stat().st_size if target.exists() else 0,
    })

@app.get("/api/alias/<site_image_id>")
def api_alias_metadata(site_image_id: str):
    site_image_id = valid_site_image_id(site_image_id)
    image_id = alias_image_id(site_image_id)
    if not image_id:
        return jsonify({"error": "Website image alias is not mapped."}), 404
    entry = registry_entry(image_id)
    return jsonify({
        "siteImageId": site_image_id,
        "imageId": image_id,
        "url": site_alias_url(site_image_id),
        "imageUrl": id_url(image_id),
        "revision": int((entry or {}).get("revision") or 1),
    })


@app.put("/api/alias/<site_image_id>")
def api_set_alias(site_image_id: str):
    require_csrf()
    payload = request.get_json(silent=True) or {}
    result = set_alias(site_image_id, str(payload.get("imageId") or ""))
    return jsonify({"ok": True, **result})


@app.delete("/api/alias/<site_image_id>")
def api_delete_alias(site_image_id: str):
    require_csrf()
    existed = delete_alias(site_image_id)
    return jsonify({"ok": True, "deleted": existed, "siteImageId": valid_site_image_id(site_image_id)})


def serve_registered_image(image_id: str, *, public_id: str | None = None):
    image_id = valid_image_id(image_id)
    entry = registry_entry(image_id)
    if entry is None:
        return jsonify({"error": "Unknown image ID."}), 404
    try:
        relative = normalize_relative(str(entry.get("path") or ""), allow_empty=False)
    except ValueError:
        return jsonify({"error": "Image ID has an invalid storage path."}), 500
    source = disk_path(relative)
    if not source.exists() or not is_image_file(source):
        return jsonify({"error": "Image file is missing."}), 404

    max_dimension = request.args.get("max", default=0, type=int) or 0
    max_dimension = max(0, min(max_dimension, 8192))
    served = resized_path(image_id, entry, source, max_dimension) if max_dimension else source
    revision = int(entry.get("revision") or 1)
    etag_id = public_id or image_id
    etag = f"{etag_id}-{image_id}-r{revision}-m{max_dimension or 0}"
    response = send_file(served, conditional=True, etag=etag, max_age=0, download_name=source.name)
    response.headers["Cache-Control"] = "public, no-cache, must-revalidate"
    response.headers["X-Shin-Image-Id"] = image_id
    response.headers["X-Shin-Image-Revision"] = str(revision)
    if public_id:
        response.headers["X-Shin-Site-Image-Id"] = public_id
    return response


@app.get("/s/<site_image_id>")
def public_image_by_site_alias(site_image_id: str):
    site_image_id = valid_site_image_id(site_image_id)
    image_id = alias_image_id(site_image_id)
    if not image_id:
        return jsonify({"error": "Website image alias is not mapped."}), 404
    return serve_registered_image(image_id, public_id=site_image_id)


@app.get("/i/<image_id>")
def public_image_by_id(image_id: str):
    return serve_registered_image(image_id)


@app.get("/api/image/<image_id>")
def api_image_metadata(image_id: str):
    image_id = valid_image_id(image_id)
    entry = registry_entry(image_id)
    if entry is None:
        return jsonify({"error": "Unknown image ID."}), 404
    relative = normalize_relative(str(entry.get("path") or ""), allow_empty=False)
    target = disk_path(relative)
    width, height = image_dimensions(target) if target.exists() else (None, None)
    return jsonify({
        "id": image_id,
        "path": relative_string(relative),
        "url": id_url(image_id),
        "directUrl": public_url(relative),
        "revision": int(entry.get("revision") or 1),
        "updatedAt": entry.get("updatedAt"),
        "exists": target.exists(),
        "width": width,
        "height": height,
        "size": target.stat().st_size if target.exists() else 0,
    })


@app.post("/api/image/<image_id>/replace")
def api_replace_image(image_id: str):
    require_csrf()
    image_id = valid_image_id(image_id)
    entry = registry_entry(image_id)
    if entry is None:
        return jsonify({"error": "Unknown image ID."}), 404
    item = request.files.get("file")
    if item is None or not item.filename:
        raise ValueError("Select a replacement image.")
    filename = secure_filename(item.filename)
    extension = Path(filename).suffix.lower()
    if not filename or extension not in IMAGE_EXTENSIONS:
        raise ValueError("Unsupported replacement image extension.")

    old_rel = normalize_relative(str(entry.get("path") or ""), allow_empty=False)
    old_target = disk_path(old_rel)
    new_rel = old_rel.parent / filename
    new_target = disk_path(new_rel)
    if new_target != old_target and new_target.exists():
        return jsonify({"error": "A file with that replacement name already exists in this folder."}), 409

    save_uploaded_file(item, new_target)
    if new_target != old_target:
        old_target.unlink(missing_ok=True)

    now = int(time.time())
    with registry_lock(exclusive=True):
        registry = load_registry_unlocked()
        current = registry["images"].get(image_id)
        if not isinstance(current, dict):
            new_target.unlink(missing_ok=True)
            return jsonify({"error": "Image ID disappeared during replacement."}), 409
        current["path"] = relative_string(new_rel)
        current["revision"] = int(current.get("revision") or 1) + 1
        current["updatedAt"] = now
        save_registry_unlocked(registry)
        revision = current["revision"]
    clear_derivatives(image_id)
    return jsonify({
        "ok": True,
        "id": image_id,
        "path": relative_string(new_rel),
        "url": id_url(image_id),
        "directUrl": public_url(new_rel),
        "revision": revision,
    })


@app.post("/api/folders")
def api_create_folder():
    require_csrf()
    payload = request.get_json(silent=True) or {}
    parent = normalize_relative(payload.get("parent"))
    name = secure_filename((payload.get("name") or "").strip())
    if not name or name.startswith("."):
        raise ValueError("Invalid folder name.")
    relative = parent / name
    target = disk_path(relative)
    target.mkdir(parents=False, exist_ok=False)
    return jsonify({"ok": True, "path": relative_string(relative)}), 201


@app.post("/api/upload")
def api_upload():
    require_csrf()
    folder = normalize_relative(request.form.get("path"))
    destination = disk_path(folder)
    if not destination.exists() or not destination.is_dir():
        return jsonify({"error": "Destination folder does not exist."}), 404

    overwrite = request.form.get("overwrite", "false").lower() in {"1", "true", "yes", "on"}
    uploaded = request.files.getlist("files")
    if not uploaded or all(not item.filename for item in uploaded):
        raise ValueError("Select at least one image.")

    results = []
    for item in uploaded:
        if not item.filename:
            continue
        filename = secure_filename(item.filename)
        if not filename:
            results.append({"name": item.filename, "ok": False, "error": "Invalid filename."})
            continue
        extension = Path(filename).suffix.lower()
        if extension not in IMAGE_EXTENSIONS:
            results.append({"name": filename, "ok": False, "error": "Unsupported image extension."})
            continue

        relative = folder / filename
        target = disk_path(relative)
        if target.exists() and not overwrite:
            results.append({"name": filename, "ok": False, "error": "File already exists."})
            continue

        existed = target.exists()
        try:
            save_uploaded_file(item, target)
            image_id, _entry = update_registry_for_path(relative, content_changed=existed)
            results.append({
                "name": filename,
                "ok": True,
                "path": relative_string(relative),
                "id": image_id,
                "url": id_url(image_id),
                "directUrl": public_url(relative),
            })
        except ValueError as exc:
            results.append({"name": filename, "ok": False, "error": str(exc)})

    return jsonify({"results": results}), 200


@app.post("/api/move")
def api_move():
    require_csrf()
    payload = request.get_json(silent=True) or {}
    source_rel = normalize_relative(payload.get("source"), allow_empty=False)
    dest_rel = normalize_relative(payload.get("destination"), allow_empty=False)
    source = disk_path(source_rel)
    destination = disk_path(dest_rel)

    if not source.exists():
        return jsonify({"error": "Source does not exist."}), 404
    if source.is_symlink():
        raise ValueError("Symlinks are not supported.")
    if destination.exists():
        return jsonify({"error": "Destination already exists."}), 409
    if not destination.parent.exists() or not destination.parent.is_dir():
        return jsonify({"error": "Destination parent folder does not exist."}), 400

    source.rename(destination)

    source_text = relative_string(source_rel)
    dest_text = relative_string(dest_rel)
    moved_ids: list[str] = []
    with registry_lock(exclusive=True):
        registry = load_registry_unlocked()
        now = int(time.time())
        for image_id, entry in registry["images"].items():
            path = str(entry.get("path") or "")
            if path == source_text or path.startswith(source_text + "/"):
                suffix = path[len(source_text):].lstrip("/")
                entry["path"] = dest_text if not suffix else f"{dest_text}/{suffix}"
                entry["updatedAt"] = now
                moved_ids.append(image_id)
        if moved_ids:
            save_registry_unlocked(registry)

    response = {"ok": True, "path": dest_text, "ids": moved_ids}
    if destination.is_file():
        image_id = moved_ids[0] if moved_ids else register_path(dest_rel)[0]
        response["id"] = image_id
        response["url"] = id_url(image_id)
        response["directUrl"] = public_url(dest_rel)
    return jsonify(response)


@app.delete("/api/item")
def api_delete():
    require_csrf()
    payload = request.get_json(silent=True) or {}
    relative = normalize_relative(payload.get("path"), allow_empty=False)
    target = disk_path(relative)
    if not target.exists():
        return jsonify({"error": "Item does not exist."}), 404
    if target.is_symlink():
        raise ValueError("Symlinks are not supported.")

    relative_text = relative_string(relative)
    if target.is_dir():
        if any(target.iterdir()):
            return jsonify({"error": "Folder is not empty."}), 409
        target.rmdir()
    else:
        target.unlink()

    removed_ids: list[str] = []
    with registry_lock(exclusive=True):
        registry = load_registry_unlocked()
        for image_id, entry in list(registry["images"].items()):
            path = str(entry.get("path") or "")
            if path == relative_text or path.startswith(relative_text + "/"):
                registry["images"].pop(image_id, None)
                removed_ids.append(image_id)
        if removed_ids:
            save_registry_unlocked(registry)
    for image_id in removed_ids:
        clear_derivatives(image_id)
    return jsonify({"ok": True, "removedIds": removed_ids})


reconcile_registry()

if __name__ == "__main__":
    app.run(host=settings.bind_host, port=settings.bind_port, debug=False)
