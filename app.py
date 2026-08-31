from __future__ import annotations

import hmac
import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

try:
    from PIL import Image
except Exception:  # Pillow is optional at runtime for dimensions only.
    Image = None


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
    if request.path.startswith("/static/") or request.endpoint in {"login", "health"}:
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
            items.append({
                "type": "image",
                "name": child.name,
                "path": relative_string(child_rel),
                "size": stat.st_size,
                "modified": int(stat.st_mtime),
                "width": width,
                "height": height,
                "url": public_url(child_rel),
                "thumbnailUrl": thumbnail_url(child_rel),
            })

    parent = relative.parent if relative.parts else PurePosixPath()
    return jsonify({
        "path": relative_string(relative),
        "parent": relative_string(parent),
        "items": items,
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

        tmp = target.with_name(f".{target.name}.{secrets.token_hex(6)}.upload")
        try:
            item.save(tmp)
            # Validate common formats when Pillow understands them; unsupported codecs remain uploadable.
            if Image is not None:
                try:
                    with Image.open(tmp) as image:
                        image.verify()
                except Exception as exc:
                    tmp.unlink(missing_ok=True)
                    results.append({"name": filename, "ok": False, "error": f"Image validation failed: {exc}"})
                    continue
            os.replace(tmp, target)
            results.append({
                "name": filename,
                "ok": True,
                "path": relative_string(relative),
                "url": public_url(relative),
            })
        finally:
            tmp.unlink(missing_ok=True)

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
    response = {"ok": True, "path": relative_string(dest_rel)}
    if destination.is_file():
        response["url"] = public_url(dest_rel)
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

    if target.is_dir():
        if any(target.iterdir()):
            return jsonify({"error": "Folder is not empty."}), 409
        target.rmdir()
    else:
        target.unlink()
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host=settings.bind_host, port=settings.bind_port, debug=False)
