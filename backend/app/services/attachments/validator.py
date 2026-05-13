from pathlib import Path
import mimetypes
import re

from fastapi import HTTPException, UploadFile

from app.core.config import settings
from app.services.attachments.constants import CATEGORY_BY_EXTENSION, ALL_SUPPORTED_EXTENSIONS


_SANITIZE_PATTERN = re.compile(r"[^a-zA-Z0-9._-]+")
_TABLE_CONTENT_TYPES = {
    "text/csv",
    "application/csv",
    "text/tab-separated-values",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel.sheet.macroenabled.12",
    "application/vnd.ms-excel.sheet.binary.macroenabled.12",
    "application/vnd.oasis.opendocument.spreadsheet",
}


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
        "pdf": getattr(settings, "MAX_PDF_UPLOAD_MB", base_mb),
    }
    return int(per_type_overrides.get(category, base_mb) * 1024 * 1024)


def detect_type(filename: str, content_type: str | None = None) -> str:
    ext = Path(filename).suffix.lower().strip()
    file_type = CATEGORY_BY_EXTENSION.get(ext)
    if file_type and ext in ALL_SUPPORTED_EXTENSIONS:
        return file_type

    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    guessed_content_type, _ = mimetypes.guess_type(filename)
    effective_content_type = normalized_content_type or (guessed_content_type or "").lower()

    # Keep video support extensible: any video/* MIME is accepted as video.
    if effective_content_type.startswith("video/"):
        return "video"

    # Accept common spreadsheet MIME types even when extension is missing/unusual.
    if (
        effective_content_type in _TABLE_CONTENT_TYPES
        or effective_content_type.startswith("application/vnd.ms-excel")
    ):
        return "table"

    supported = ", ".join(sorted(ALL_SUPPORTED_EXTENSIONS))
    raise HTTPException(
        status_code=400,
        detail=(
            f"Unsupported file type: {ext or 'unknown'}"
            f" (content-type: {effective_content_type or 'unknown'}). "
            f"Supported extensions: {supported}. "
            "Additionally, any file detected as video/* is accepted as a video attachment, "
            "and common spreadsheet MIME types are accepted as table attachments."
        ),
    )


async def validate_upload(upload: UploadFile) -> tuple[str, int]:
    if not upload.filename:
        raise HTTPException(status_code=400, detail="Missing file name")

    file_type = detect_type(upload.filename, upload.content_type)
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
