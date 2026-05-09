from pathlib import Path
import uuid

from app.core.config import settings
from app.services.attachments.validator import sanitize_filename


def uploads_root() -> Path:
    root = Path(settings.UPLOAD_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def bucket_dir(file_type: str) -> Path:
    folder = uploads_root() / file_type
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def thumbnails_dir() -> Path:
    folder = uploads_root() / "thumbnails"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def make_storage_path(file_type: str, original_filename: str) -> Path:
    safe_name = sanitize_filename(original_filename)
    return bucket_dir(file_type) / f"{uuid.uuid4().hex}_{safe_name}"


def make_thumbnail_path(original_name: str) -> Path:
    safe_name = sanitize_filename(original_name)
    stem = Path(safe_name).stem
    return thumbnails_dir() / f"{uuid.uuid4().hex}_{stem}.jpg"
