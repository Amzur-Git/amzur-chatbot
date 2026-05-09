from pathlib import Path
import re

from fastapi import HTTPException, UploadFile

from app.core.config import settings
from app.services.attachments.constants import CATEGORY_BY_EXTENSION, ALL_SUPPORTED_EXTENSIONS


_SANITIZE_PATTERN = re.compile(r"[^a-zA-Z0-9._-]+")


def sanitize_filename(filename: str) -> str:
    clean_name = _SANITIZE_PATTERN.sub("_", Path(filename).name)
    return clean_name[:255] or "file"


def _category_limit_bytes(category: str) -> int:
    base_mb = settings.MAX_UPLOAD_MB
    per_type_overrides = {
        "image": getattr(settings, "MAX_IMAGE_UPLOAD_MB", base_mb),
        "video": getattr(settings, "MAX_VIDEO_UPLOAD_MB", base_mb),
        "table": getattr(settings, "MAX_TABLE_UPLOAD_MB", base_mb),
        "code": getattr(settings, "MAX_CODE_UPLOAD_MB", base_mb),
        "formula": getattr(settings, "MAX_FORMULA_UPLOAD_MB", base_mb),
    }
    return int(per_type_overrides.get(category, base_mb) * 1024 * 1024)


def detect_type(filename: str) -> str:
    ext = Path(filename).suffix.lower().strip()
    file_type = CATEGORY_BY_EXTENSION.get(ext)
    if not file_type or ext not in ALL_SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(ALL_SUPPORTED_EXTENSIONS))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext or 'unknown'}. Supported extensions: {supported}",
        )
    return file_type


async def validate_upload(upload: UploadFile) -> tuple[str, int]:
    if not upload.filename:
        raise HTTPException(status_code=400, detail="Missing file name")

    file_type = detect_type(upload.filename)
    payload = await upload.read()
    size = len(payload)

    if size <= 0:
        raise HTTPException(status_code=400, detail="Empty file upload")

    max_allowed = _category_limit_bytes(file_type)
    if size > max_allowed:
        max_mb = max_allowed // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {max_mb}MB limit for {file_type} attachments",
        )

    await upload.seek(0)
    return file_type, size
