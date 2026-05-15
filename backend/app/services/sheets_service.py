from __future__ import annotations

import json
import re
import shutil
import uuid
import io
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import pandas as pd
from fastapi import HTTPException

from app.core.config import settings

try:
    import gspread
except ImportError:  # pragma: no cover - environment dependent
    gspread = None


_SHEET_ID_PATTERN = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
_SUPPORTED_TABLE_EXTENSIONS = {".csv", ".tsv", ".xls", ".xlsx", ".xlsm", ".xlsb", ".ods"}


def _resolve_temp_upload_dir() -> Path:
    target = Path(settings.SHEETS_UPLOAD_DIR)
    try:
        target.mkdir(parents=True, exist_ok=True)
        return target
    except Exception:
        fallback = Path(settings.UPLOAD_DIR) / "sheets_temp"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _read_service_account_dict() -> dict:
    payload: Optional[dict] = None

    raw_json = (settings.GOOGLE_SERVICE_ACCOUNT_JSON or "").strip()
    if raw_json:
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError as error:
            raise HTTPException(
                status_code=500,
                detail="Invalid GOOGLE_SERVICE_ACCOUNT_JSON format. Expected valid JSON object.",
            ) from error

        if not isinstance(parsed, dict):
            raise HTTPException(
                status_code=500,
                detail="Invalid GOOGLE_SERVICE_ACCOUNT_JSON payload. Expected a JSON object.",
            )

        payload = parsed
    else:
        service_account_file = (settings.GOOGLE_SERVICE_ACCOUNT_FILE or "").strip()
        if not service_account_file:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Google Sheets credentials are not configured. Set GOOGLE_SERVICE_ACCOUNT_JSON "
                    "or GOOGLE_SERVICE_ACCOUNT_FILE."
                ),
            )

        file_path = Path(service_account_file).expanduser()
        if not file_path.is_absolute():
            file_path = (Path.cwd() / file_path).resolve()

        if not file_path.exists():
            raise HTTPException(
                status_code=500,
                detail=(
                    "GOOGLE_SERVICE_ACCOUNT_FILE does not exist: "
                    f"{file_path}"
                ),
            )

        try:
            parsed = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise HTTPException(
                status_code=500,
                detail="Invalid JSON in GOOGLE_SERVICE_ACCOUNT_FILE.",
            ) from error
        except OSError as error:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to read GOOGLE_SERVICE_ACCOUNT_FILE: {error}",
            ) from error

        if not isinstance(parsed, dict):
            raise HTTPException(
                status_code=500,
                detail="Invalid GOOGLE_SERVICE_ACCOUNT_FILE payload. Expected a JSON object.",
            )

        payload = parsed

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=500,
            detail="Google Sheets service account payload is invalid.",
        )

    return payload


def _parse_sheet_url(sheet_url: str) -> tuple[str, Optional[int]]:
    match = _SHEET_ID_PATTERN.search(sheet_url)
    if not match:
        raise HTTPException(
            status_code=400,
            detail="Invalid Google Sheets URL. Expected format containing /spreadsheets/d/<sheet_id>.",
        )

    sheet_id = match.group(1)
    gid: Optional[int] = None

    parsed = urlparse(sheet_url)
    query = parse_qs(parsed.query)
    gid_values = query.get("gid")
    if gid_values:
        try:
            gid = int(gid_values[0])
        except (TypeError, ValueError):
            gid = None

    if gid is None and parsed.fragment.startswith("gid="):
        try:
            gid = int(parsed.fragment.split("=", 1)[1])
        except (TypeError, ValueError):
            gid = None

    return sheet_id, gid


def _normalize_headers(raw_headers: list[str]) -> list[str]:
    normalized: list[str] = []
    used: dict[str, int] = {}

    for idx, header in enumerate(raw_headers, start=1):
        candidate = (str(header).strip() if header is not None else "") or f"column_{idx}"
        count = used.get(candidate, 0)
        if count > 0:
            unique_name = f"{candidate}_{count + 1}"
        else:
            unique_name = candidate
        used[candidate] = count + 1
        normalized.append(unique_name)

    return normalized


def _postprocess_dataframe(frame: pd.DataFrame) -> pd.DataFrame:
    # Normalize blank cells to missing values and ask pandas to infer friendly dtypes.
    frame = frame.replace(r"^\s*$", pd.NA, regex=True)
    return frame.convert_dtypes()


def _sheet_to_dataframe(worksheet) -> pd.DataFrame:
    try:
        values = worksheet.get_all_values()
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Failed to fetch worksheet values: {error}") from error

    if not values:
        raise HTTPException(status_code=400, detail="Google Sheet is empty.")

    headers = _normalize_headers(values[0])
    rows = values[1:]

    if not rows:
        raise HTTPException(status_code=400, detail="Google Sheet has no data rows.")

    frame = pd.DataFrame(rows, columns=headers)
    return _postprocess_dataframe(frame)


def _open_google_worksheet(sheet_url: str):
    if gspread is None:
        raise HTTPException(
            status_code=500,
            detail="gspread is required for Google Sheets support. Install it in the backend environment.",
        )

    sheet_id, requested_gid = _parse_sheet_url(sheet_url)
    service_account = _read_service_account_dict()

    try:
        client = gspread.service_account_from_dict(service_account)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="Failed to initialize Google Sheets client. Check service account credentials.",
        ) from error

    try:
        spreadsheet = client.open_by_key(sheet_id)
    except Exception as error:
        message = str(error)
        lowered = message.lower()
        if "not found" in lowered or "permission" in lowered or "403" in lowered:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Unable to access Google Sheet. Verify the URL and share the sheet with the "
                    "service account email."
                ),
            ) from error
        raise HTTPException(status_code=502, detail=f"Google Sheets API error: {error}") from error

    try:
        if requested_gid is None:
            worksheet = spreadsheet.get_worksheet(0)
        else:
            worksheet = next((ws for ws in spreadsheet.worksheets() if ws.id == requested_gid), None)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Failed to inspect worksheet tabs: {error}") from error

    if worksheet is None:
        raise HTTPException(status_code=404, detail="Worksheet tab not found for the provided gid.")

    return spreadsheet, worksheet


def load_sheet_as_dataframe(sheet_url: str) -> pd.DataFrame:
    _, worksheet = _open_google_worksheet(sheet_url)
    return _sheet_to_dataframe(worksheet)


def load_sheet_as_dataframe_with_metadata(sheet_url: str) -> tuple[pd.DataFrame, str]:
    _, worksheet = _open_google_worksheet(sheet_url)
    return _sheet_to_dataframe(worksheet), worksheet.title


def load_public_sheet_as_dataframe_with_metadata(sheet_url: str) -> tuple[pd.DataFrame, str]:
    """
    Load a publicly accessible Google Sheet via CSV export without OAuth/service-account auth.
    """
    sheet_id, requested_gid = _parse_sheet_url(sheet_url)
    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    if requested_gid is not None:
        export_url += f"&gid={requested_gid}"

    request = Request(export_url, headers={"User-Agent": "amzur-chatbot/1.0"})
    try:
        with urlopen(request, timeout=20) as response:
            content = response.read().decode("utf-8-sig", errors="replace")
    except HTTPError as error:
        if error.code in {401, 403}:
            raise HTTPException(
                status_code=403,
                detail="Public sheet export is not accessible. Verify the sheet is shared for public viewing.",
            ) from error
        raise HTTPException(status_code=502, detail=f"Failed to download public sheet CSV: HTTP {error.code}") from error
    except URLError as error:
        raise HTTPException(status_code=502, detail=f"Failed to download public sheet CSV: {error}") from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Unexpected public sheet download error: {error}") from error

    # If Google returns an HTML interstitial/login page, it's not publicly readable as CSV.
    if "<html" in content[:500].lower():
        raise HTTPException(
            status_code=403,
            detail="Sheet is not accessible via public CSV export. Verify link sharing allows public viewing.",
        )

    try:
        frame = pd.read_csv(io.StringIO(content))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Failed to parse public sheet CSV: {error}") from error

    if frame.empty:
        raise HTTPException(status_code=400, detail="Google Sheet is empty.")

    frame.columns = _normalize_headers([str(col) for col in frame.columns.tolist()])
    frame = _postprocess_dataframe(frame)
    sheet_name = f"Google Sheet (gid={requested_gid})" if requested_gid is not None else "Google Sheet"
    return frame, sheet_name


def _read_local_table(file_path: Path) -> pd.DataFrame:
    suffix = file_path.suffix.lower()

    try:
        if suffix == ".csv":
            frame = pd.read_csv(file_path)
        elif suffix == ".tsv":
            frame = pd.read_csv(file_path, sep="\t")
        elif suffix == ".xls":
            frame = pd.read_excel(file_path, engine="xlrd")
        elif suffix in {".xlsx", ".xlsm"}:
            frame = pd.read_excel(file_path, engine="openpyxl")
        elif suffix == ".xlsb":
            frame = pd.read_excel(file_path, engine="pyxlsb")
        elif suffix == ".ods":
            frame = pd.read_excel(file_path, engine="odf")
        else:
            frame = pd.read_excel(file_path)
    except ImportError as error:
        raise HTTPException(
            status_code=400,
            detail=f"Missing optional dependency to parse {suffix or 'table'} files: {error}",
        ) from error
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Failed to read uploaded table file: {error}") from error

    if frame.empty:
        raise HTTPException(status_code=400, detail="Uploaded table file has no data rows.")

    return _postprocess_dataframe(frame)


def load_file_as_dataframe(file_path: str) -> pd.DataFrame:
    source = Path(file_path)
    if not source.exists():
        raise HTTPException(status_code=404, detail="Uploaded file not found on disk.")

    if source.suffix.lower() not in _SUPPORTED_TABLE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported file format. Use CSV or Excel-compatible files.")

    temp_dir = _resolve_temp_upload_dir()
    staged_name = f"{uuid.uuid4().hex}_{source.name}"
    staged_path = temp_dir / staged_name

    try:
        shutil.copy2(source, staged_path)
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Failed to stage uploaded file: {error}") from error

    try:
        return _read_local_table(staged_path)
    finally:
        try:
            staged_path.unlink(missing_ok=True)
        except Exception:
            pass
