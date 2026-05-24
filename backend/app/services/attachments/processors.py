from __future__ import annotations

import ast
import base64
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import HTTPException

from app.services.attachments.constants import LANGUAGE_BY_EXTENSION
from app.services.attachments.storage import make_thumbnail_path

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

try:
    from pygments.lexers import get_lexer_for_filename
except Exception:  # pragma: no cover
    get_lexer_for_filename = None

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None


class ImageProcessor:
    MAX_DIMENSION = 1920
    MAX_BASE64_PREVIEW_CHARS = 8192

    @staticmethod
    def _base64_preview(file_path: Path) -> tuple[str, bool]:
        encoded = base64.b64encode(file_path.read_bytes()).decode("utf-8")
        return encoded[: ImageProcessor.MAX_BASE64_PREVIEW_CHARS], (
            len(encoded) > ImageProcessor.MAX_BASE64_PREVIEW_CHARS
        )

    @staticmethod
    def _extract_svg_size(svg_text: str) -> tuple[int | None, int | None]:
        width_match = re.search(r"\bwidth\s*=\s*['\"]([0-9.]+)", svg_text, re.IGNORECASE)
        height_match = re.search(r"\bheight\s*=\s*['\"]([0-9.]+)", svg_text, re.IGNORECASE)
        if width_match and height_match:
            try:
                return int(float(width_match.group(1))), int(float(height_match.group(1)))
            except ValueError:
                pass

        viewbox_match = re.search(
            r"\bviewBox\s*=\s*['\"]\s*[0-9.\-]+\s+[0-9.\-]+\s+([0-9.\-]+)\s+([0-9.\-]+)\s*['\"]",
            svg_text,
            re.IGNORECASE,
        )
        if viewbox_match:
            try:
                return int(float(viewbox_match.group(1))), int(float(viewbox_match.group(2)))
            except ValueError:
                pass

        return None, None

    @staticmethod
    def process(file_path: Path) -> dict[str, Any]:
        suffix = file_path.suffix.lower()
        preview, preview_truncated = ImageProcessor._base64_preview(file_path)

        if suffix == ".svg":
            svg_text = file_path.read_text(encoding="utf-8", errors="ignore")
            width, height = ImageProcessor._extract_svg_size(svg_text)
            return {
                "format": "svg",
                "width": width,
                "height": height,
                "mode": "vector",
                "base64_preview": preview,
                "base64_truncated": preview_truncated,
            }

        if Image is None:
            return {
                "format": suffix.lstrip(".") or "unknown",
                "width": None,
                "height": None,
                "mode": "unknown",
                "processing_fallback": "pillow_not_installed",
                "base64_preview": preview,
                "base64_truncated": preview_truncated,
            }

        try:
            with Image.open(file_path) as image:
                width, height = image.size
                image_format = (image.format or suffix.lstrip(".") or "unknown").lower()
                metadata: dict[str, Any] = {
                    "format": image_format,
                    "width": width,
                    "height": height,
                    "mode": image.mode,
                }

                # Keep payload lightweight for model context.
                image.thumbnail((ImageProcessor.MAX_DIMENSION, ImageProcessor.MAX_DIMENSION))
                metadata["base64_preview"] = preview
                metadata["base64_truncated"] = preview_truncated
                return metadata
        except Exception:
            # If decoding fails, preserve upload with minimal metadata instead of failing hard.
            return {
                "format": suffix.lstrip(".") or "unknown",
                "width": None,
                "height": None,
                "mode": "unknown",
                "processing_fallback": "image_decode_failed",
                "base64_preview": preview,
                "base64_truncated": preview_truncated,
            }


class VideoProcessor:
    MAX_KEYFRAMES = 3
    MAX_FRAME_WIDTH = 960
    JPEG_QUALITY = 80

    @staticmethod
    def _ffmpeg_binary() -> str | None:
        return shutil.which("ffmpeg")

    @staticmethod
    def _transcode_to_mp4(file_path: Path) -> Path | None:
        ffmpeg_bin = VideoProcessor._ffmpeg_binary()
        if ffmpeg_bin is None:
            return None

        temp_dir = Path(tempfile.gettempdir()) / "amzur_video_transcodes"
        temp_dir.mkdir(parents=True, exist_ok=True)
        output_path = temp_dir / f"{file_path.stem}_{uuid.uuid4().hex}.mp4"

        command = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(file_path),
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except Exception:
            return None

        if completed.returncode != 0 or not output_path.exists():
            return None

        return output_path

    @staticmethod
    def _frame_to_base64(frame: Any) -> str | None:
        height, width = frame.shape[:2]
        if width > VideoProcessor.MAX_FRAME_WIDTH and width > 0:
            ratio = VideoProcessor.MAX_FRAME_WIDTH / float(width)
            resized_height = max(1, int(height * ratio))
            frame = cv2.resize(frame, (VideoProcessor.MAX_FRAME_WIDTH, resized_height))

        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), VideoProcessor.JPEG_QUALITY],
        )
        if not ok:
            return None
        return base64.b64encode(encoded.tobytes()).decode("utf-8")

    @staticmethod
    def process(file_path: Path) -> dict[str, Any]:
        if cv2 is None:
            raise HTTPException(
                status_code=500,
                detail="OpenCV is required for video processing. Install opencv-python or opencv-python-headless.",
            )

        processed_path = file_path
        transcoded_path: Path | None = None

        capture = cv2.VideoCapture(str(processed_path))
        if not capture.isOpened():
            transcoded_path = VideoProcessor._transcode_to_mp4(file_path)
            if transcoded_path is not None:
                processed_path = transcoded_path
                capture = cv2.VideoCapture(str(processed_path))

        if not capture.isOpened():
            raise HTTPException(
                status_code=400,
                detail=(
                    "Unable to open video file. The container/codec may be unsupported by OpenCV. "
                    "Install ffmpeg on the server to enable automatic transcoding fallback."
                ),
            )

        fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration_seconds = float(frame_count / fps) if fps > 0 else 0.0

        keyframe_indices: list[int] = [0]
        if frame_count > 1:
            keyframe_indices.extend(
                [
                    max(0, int(frame_count * 0.5)),
                    max(0, int(frame_count - 1)),
                ]
            )
        # Preserve order and avoid duplicate probes for short videos.
        keyframe_indices = list(dict.fromkeys(keyframe_indices))[: VideoProcessor.MAX_KEYFRAMES]

        keyframes_base64: list[str] = []
        thumbnail = None

        for index in keyframe_indices:
            capture.set(cv2.CAP_PROP_POS_FRAMES, float(index))
            ok, frame = capture.read()
            if not ok or frame is None:
                continue

            encoded_frame = VideoProcessor._frame_to_base64(frame)
            if encoded_frame:
                keyframes_base64.append(encoded_frame)

            if thumbnail is None:
                thumbnail_path = make_thumbnail_path(file_path.name)
                cv2.imwrite(str(thumbnail_path), frame)
                thumbnail = str(thumbnail_path)

        capture.release()

        result = {
            "fps": round(fps, 3),
            "frame_count": int(frame_count),
            "duration_seconds": round(duration_seconds, 3),
            "width": width,
            "height": height,
            "thumbnail_path": thumbnail,
            "keyframes_base64": keyframes_base64,
            "keyframe_count": len(keyframes_base64),
            "processing_path": str(processed_path),
            "transcoded_for_processing": bool(transcoded_path),
        }

        if transcoded_path is not None:
            try:
                transcoded_path.unlink(missing_ok=True)
            except Exception:
                pass

        return result


class TableProcessor:
    MAX_ROWS_FOR_FULL_PAYLOAD = 150

    @staticmethod
    def _normalize_for_json(value: Any) -> Any:
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if isinstance(value, pd.Timedelta):
            return str(value)
        if isinstance(value, dict):
            return {str(key): TableProcessor._normalize_for_json(item) for key, item in value.items()}
        if isinstance(value, list):
            return [TableProcessor._normalize_for_json(item) for item in value]
        if isinstance(value, tuple):
            return [TableProcessor._normalize_for_json(item) for item in value]

        # Handle numpy scalar types without importing numpy directly.
        if hasattr(value, "item") and callable(getattr(value, "item")):
            try:
                return TableProcessor._normalize_for_json(value.item())
            except Exception:
                pass

        try:
            if pd.isna(value):
                return ""
        except Exception:
            pass

        return value

    @staticmethod
    def _read_frame(file_path: Path) -> pd.DataFrame:
        suffix = file_path.suffix.lower()
        try:
            if suffix == ".csv":
                return pd.read_csv(file_path)
            if suffix == ".tsv":
                return pd.read_csv(file_path, sep="\t")
            if suffix == ".xls":
                return pd.read_excel(file_path, engine="xlrd")
            if suffix in {".xlsx", ".xlsm"}:
                return pd.read_excel(file_path, engine="openpyxl")
            if suffix == ".xlsb":
                return pd.read_excel(file_path, engine="pyxlsb")
            if suffix == ".ods":
                return pd.read_excel(file_path, engine="odf")

            # Fallback for future spreadsheet extensions if validation allows them.
            return pd.read_excel(file_path)
        except ImportError as error:
            missing_package_match = re.search(r"No module named ['\"]([^'\"]+)['\"]", str(error))
            missing_package = missing_package_match.group(1) if missing_package_match else None
            if missing_package:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Failed to process file: missing optional dependency '{missing_package}'. "
                        f"Install it in the backend environment to read {suffix or 'spreadsheet'} files."
                    ),
                ) from error
            raise HTTPException(status_code=400, detail=f"Failed to process file: {error}") from error
        except Exception as error:
            raise HTTPException(status_code=400, detail=f"Failed to process file: {error}") from error

    @staticmethod
    def process(file_path: Path) -> dict[str, Any]:
        frame = TableProcessor._read_frame(file_path)

        frame = frame.fillna("")
        row_count, col_count = frame.shape
        columns = [str(column) for column in frame.columns.tolist()]
        sample = TableProcessor._normalize_for_json(frame.head(10).to_dict(orient="records"))

        payload: dict[str, Any] = {
            "rows": int(row_count),
            "columns": int(col_count),
            "column_names": columns,
            "sample": sample,
            "full_included": row_count <= TableProcessor.MAX_ROWS_FOR_FULL_PAYLOAD,
        }

        if row_count <= TableProcessor.MAX_ROWS_FOR_FULL_PAYLOAD:
            payload["records"] = TableProcessor._normalize_for_json(frame.to_dict(orient="records"))

        return payload


class CodeProcessor:
    @staticmethod
    def process(file_path: Path) -> dict[str, Any]:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        extension = file_path.suffix.lower()
        language = LANGUAGE_BY_EXTENSION.get(extension, "text")

        syntax_valid = True
        syntax_error = None
        if extension == ".py":
            try:
                ast.parse(content)
            except SyntaxError as error:
                syntax_valid = False
                syntax_error = str(error)
        elif get_lexer_for_filename is not None:
            try:
                get_lexer_for_filename(file_path.name)
            except Exception:
                # Lexer detection failure is informational only.
                pass

        return {
            "language": language,
            "line_count": len(content.splitlines()),
            "char_count": len(content),
            "syntax_valid": syntax_valid,
            "syntax_error": syntax_error,
            "content": content,
        }


class FormulaProcessor:
    @staticmethod
    def process(file_path: Path) -> dict[str, Any]:
        latex = file_path.read_text(encoding="utf-8", errors="ignore").strip()
        if not latex:
            raise HTTPException(status_code=400, detail="LaTeX file is empty")

        return {
            "latex": latex,
            "syntax_valid": FormulaProcessor._is_valid_latex(latex),
            "length": len(latex),
        }

    @staticmethod
    def from_text(latex: str) -> dict[str, Any]:
        normalized = (latex or "").strip()
        if not normalized:
            raise HTTPException(status_code=400, detail="Formula text is empty")

        return {
            "latex": normalized,
            "syntax_valid": FormulaProcessor._is_valid_latex(normalized),
            "length": len(normalized),
        }

    @staticmethod
    def _is_valid_latex(latex: str) -> bool:
        pairs = {"{": "}", "(": ")", "[": "]"}
        stack: list[str] = []
        for character in latex:
            if character in pairs:
                stack.append(pairs[character])
            elif character in pairs.values():
                if not stack or stack.pop() != character:
                    return False
        return not stack


class PdfProcessor:
    MAX_PREVIEW_CHARS = 2000

    @staticmethod
    def process(file_path: Path) -> dict[str, Any]:
        if PdfReader is None:
            raise HTTPException(status_code=500, detail="pypdf is required for PDF processing")

        try:
            reader = PdfReader(str(file_path))
        except Exception as error:
            raise HTTPException(status_code=400, detail=f"Unable to read PDF: {error}") from error

        page_count = len(reader.pages)
        character_count = 0
        pages_with_text = 0
        preview_parts: list[str] = []

        for page in reader.pages:
            text = (page.extract_text() or "").strip()
            if not text:
                continue

            pages_with_text += 1
            character_count += len(text)

            if len("\n\n".join(preview_parts)) < PdfProcessor.MAX_PREVIEW_CHARS:
                preview_parts.append(text)

        preview_text = "\n\n".join(preview_parts)[: PdfProcessor.MAX_PREVIEW_CHARS]

        return {
            "page_count": page_count,
            "pages_with_text": pages_with_text,
            "character_count": character_count,
            "preview_text": preview_text,
            "preview_truncated": character_count > len(preview_text),
        }


def process_file(file_type: str, file_path: Path) -> dict[str, Any]:
    if file_type == "image":
        return ImageProcessor.process(file_path)
    if file_type == "video":
        return VideoProcessor.process(file_path)
    if file_type == "table":
        return TableProcessor.process(file_path)
    if file_type == "code":
        return CodeProcessor.process(file_path)
    if file_type == "formula":
        return FormulaProcessor.process(file_path)
    if file_type == "pdf":
        return PdfProcessor.process(file_path)

    raise HTTPException(status_code=400, detail=f"Unsupported processor for type: {file_type}")
