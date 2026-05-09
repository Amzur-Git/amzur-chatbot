from __future__ import annotations

import ast
import base64
import re
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
    @staticmethod
    def process(file_path: Path) -> dict[str, Any]:
        if cv2 is None:
            raise HTTPException(status_code=500, detail="opencv-python is required for video processing")

        capture = cv2.VideoCapture(str(file_path))
        if not capture.isOpened():
            raise HTTPException(status_code=400, detail="Unable to open video file")

        fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration_seconds = float(frame_count / fps) if fps > 0 else 0.0

        ok, frame = capture.read()
        thumbnail = None
        if ok:
            thumbnail_path = make_thumbnail_path(file_path.name)
            cv2.imwrite(str(thumbnail_path), frame)
            thumbnail = str(thumbnail_path)

        capture.release()

        return {
            "fps": round(fps, 3),
            "frame_count": int(frame_count),
            "duration_seconds": round(duration_seconds, 3),
            "width": width,
            "height": height,
            "thumbnail_path": thumbnail,
        }


class TableProcessor:
    MAX_ROWS_FOR_FULL_PAYLOAD = 150

    @staticmethod
    def process(file_path: Path) -> dict[str, Any]:
        suffix = file_path.suffix.lower()
        if suffix == ".csv":
            frame = pd.read_csv(file_path)
        else:
            frame = pd.read_excel(file_path)

        frame = frame.fillna("")
        row_count, col_count = frame.shape
        columns = [str(column) for column in frame.columns.tolist()]
        sample = frame.head(10).to_dict(orient="records")

        payload: dict[str, Any] = {
            "rows": int(row_count),
            "columns": int(col_count),
            "column_names": columns,
            "sample": sample,
            "full_included": row_count <= TableProcessor.MAX_ROWS_FOR_FULL_PAYLOAD,
        }

        if row_count <= TableProcessor.MAX_ROWS_FOR_FULL_PAYLOAD:
            payload["records"] = frame.to_dict(orient="records")

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

    raise HTTPException(status_code=400, detail=f"Unsupported processor for type: {file_type}")
