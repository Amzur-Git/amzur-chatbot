from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
import json
import urllib.error
import urllib.request
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.token_encryption import decrypt_token, encrypt_token
from app.models.user import User
from app.services.sheets_service import _parse_sheet_url, _sheet_to_dataframe, load_public_sheet_as_dataframe_with_metadata

try:
    import gspread
except ImportError:  # pragma: no cover - environment dependent
    gspread = None

try:
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2.credentials import Credentials
except ImportError:  # pragma: no cover - environment dependent
    GoogleAuthRequest = None
    Credentials = None


def _extract_error_text(error: Exception) -> str:
    """Return a useful message for exceptions that may stringify to an empty string."""
    message = str(error).strip()
    if message:
        return message

    response = getattr(error, "response", None)
    if response is not None:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                error_block = payload.get("error")
                if isinstance(error_block, dict):
                    details = error_block.get("message")
                    if isinstance(details, str) and details.strip():
                        return details.strip()
                if isinstance(error_block, str) and error_block.strip():
                    return error_block.strip()
            text_payload = str(payload).strip()
            if text_payload:
                return text_payload
        except Exception:
            text_payload = getattr(response, "text", "")
            if isinstance(text_payload, str) and text_payload.strip():
                return text_payload.strip()

    return error.__class__.__name__


def _is_drive_api_disabled_text(detail: str) -> bool:
    lowered = (detail or "").lower()
    return (
        "drive api has not been used" in lowered
        or "accessnotconfigured" in lowered
        or ("service_disabled" in lowered and "drive.googleapis.com" in lowered)
    )


def _build_drive_api_disabled_message(detail: str) -> str:
    return (
        "Google Drive API is disabled for this OAuth client project, so Sheets access cannot be validated. "
        "Enable Drive API in Google Cloud Console for this project and retry. "
        f"Google API detail: {detail}"
    )


def _is_sheets_api_disabled_text(detail: str) -> bool:
    lowered = (detail or "").lower()
    return (
        "sheets api has not been used" in lowered
        or "accessnotconfigured" in lowered
        or ("service_disabled" in lowered and "sheets.googleapis.com" in lowered)
    )


def _build_sheets_api_disabled_message(detail: str) -> str:
    return (
        "Google Sheets API is disabled for this OAuth client project, so worksheet access cannot be completed. "
        "Enable Sheets API in Google Cloud Console for this project and retry. "
        f"Google API detail: {detail}"
    )


def has_user_google_oauth(user: User) -> bool:
    return bool(user.google_oauth_access_token_encrypted)


def _resolve_scopes(user: User) -> list[str]:
    raw_scopes = (user.google_oauth_scopes or "").strip() or (settings.GOOGLE_OAUTH_SCOPES or "").strip()
    return [scope for scope in raw_scopes.split() if scope]


def _build_user_credentials(user: User):
    if Credentials is None:
        raise HTTPException(
            status_code=500,
            detail="google-auth is required for OAuth-based Google Sheets access. Install it in the backend environment.",
        )

    try:
        access_token = decrypt_token(user.google_oauth_access_token_encrypted or "")
    except Exception as error:
        raise HTTPException(
            status_code=401,
            detail=(
                "Stored Google OAuth token is no longer decryptable with the current server key. "
                "Please sign in with Google again to relink Sheets access."
            ),
        ) from error

    if not access_token:
        raise HTTPException(
            status_code=403,
            detail="Google Sheets access is not linked for this account. Reconnect with Google sign-in.",
        )

    refresh_token: Optional[str] = None
    if user.google_oauth_refresh_token_encrypted:
        try:
            refresh_token = decrypt_token(user.google_oauth_refresh_token_encrypted)
        except Exception as error:
            raise HTTPException(
                status_code=401,
                detail=(
                    "Stored Google refresh token is no longer decryptable with the current server key. "
                    "Please sign in with Google again to relink Sheets access."
                ),
            ) from error

    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=_resolve_scopes(user),
    )

    if user.google_oauth_token_expires_at:
        expires_at = user.google_oauth_token_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        # google-auth compares expiry against a naive UTC timestamp internally.
        # Normalize to naive UTC here to avoid offset-aware vs offset-naive TypeError.
        creds.expiry = expires_at.astimezone(timezone.utc).replace(tzinfo=None)

    return creds


async def _persist_refreshed_credentials(db: AsyncSession, user: User, creds) -> None:
    if not creds.token:
        return

    user.google_oauth_access_token_encrypted = encrypt_token(creds.token)

    if creds.refresh_token:
        user.google_oauth_refresh_token_encrypted = encrypt_token(creds.refresh_token)

    if creds.expiry:
        expiry = creds.expiry
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        user.google_oauth_token_expires_at = expiry

    user.google_oauth_updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(user)


async def _refresh_if_needed(db: AsyncSession, user: User, creds) -> None:
    if creds.valid:
        return

    if not creds.refresh_token:
        raise HTTPException(
            status_code=401,
            detail="Google access token expired and no refresh token is available. Sign in with Google again.",
        )

    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Google OAuth client credentials are not configured for token refresh.",
        )

    if GoogleAuthRequest is None:
        raise HTTPException(
            status_code=500,
            detail="google-auth transport dependencies are required to refresh Google OAuth tokens.",
        )

    try:
        creds.refresh(GoogleAuthRequest())
    except Exception as error:
        raise HTTPException(
            status_code=401,
            detail=f"Failed to refresh Google OAuth token: {error}",
        ) from error

    await _persist_refreshed_credentials(db, user, creds)


def _open_google_worksheet_with_credentials(sheet_url: str, creds):
    if gspread is None:
        raise HTTPException(
            status_code=500,
            detail="gspread is required for Google Sheets support. Install it in the backend environment.",
        )

    sheet_id, requested_gid = _parse_sheet_url(sheet_url)

    try:
        client = gspread.authorize(creds)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="Failed to initialize Google Sheets OAuth client.",
        ) from error

    try:
        spreadsheet = client.open_by_key(sheet_id)
    except gspread.exceptions.SpreadsheetNotFound as error:
        raise HTTPException(
            status_code=403,
            detail=(
                "Unable to access Google Sheet with current account permissions. "
                "Ensure this Google account can open the sheet."
            ),
        ) from error
    except gspread.exceptions.APIError as error:
        detail = _extract_error_text(error)
        lowered = detail.lower()
        if _is_drive_api_disabled_text(detail):
            raise HTTPException(
                status_code=503,
                detail=_build_drive_api_disabled_message(detail),
            ) from error
        if _is_sheets_api_disabled_text(detail):
            raise HTTPException(
                status_code=503,
                detail=_build_sheets_api_disabled_message(detail),
            ) from error
        if "insufficient authentication scopes" in lowered or "insufficientpermissions" in lowered:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Google OAuth token is missing required Sheets/Drive permissions. "
                    "Please sign in with Google again to grant Sheets access. "
                    f"Google API detail: {detail}"
                ),
            ) from error
        if "not found" in lowered or "permission" in lowered or "403" in lowered:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Unable to access Google Sheet with current account permissions. "
                    f"Google API detail: {detail}"
                ),
            ) from error
        raise HTTPException(status_code=502, detail=f"Google Sheets API error: {detail}") from error
    except Exception as error:
        message = _extract_error_text(error)
        lowered = message.lower()
        if _is_drive_api_disabled_text(message):
            raise HTTPException(
                status_code=503,
                detail=_build_drive_api_disabled_message(message),
            ) from error

        # gspread can sometimes surface a blank PermissionError. Probe Google
        # APIs directly to convert this into actionable remediation.
        try:
            sheet_id, _ = _parse_sheet_url(sheet_url)
            drive_ok, drive_payload = _drive_file_probe(creds.token, sheet_id)
            if not drive_ok:
                drive_detail = str(drive_payload.get("detail", "")).strip()
                if _is_drive_api_disabled_text(drive_detail):
                    raise HTTPException(
                        status_code=503,
                        detail=_build_drive_api_disabled_message(drive_detail),
                    ) from error

            sheets_ok, sheets_payload = _sheets_spreadsheet_probe(creds.token, sheet_id)
            if not sheets_ok:
                sheets_detail = str(sheets_payload.get("detail", "")).strip()
                lowered_sheets = sheets_detail.lower()
                if _is_sheets_api_disabled_text(sheets_detail):
                    raise HTTPException(
                        status_code=503,
                        detail=_build_sheets_api_disabled_message(sheets_detail),
                    ) from error
                if "insufficient authentication scopes" in lowered_sheets or "insufficientpermissions" in lowered_sheets:
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            "Google OAuth token is missing required Sheets/Drive permissions. "
                            "Please sign in with Google again to grant Sheets access. "
                            f"Google API detail: {sheets_detail}"
                        ),
                    ) from error

                if drive_ok:
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            "Drive access is working, but Sheets API access failed for this spreadsheet. "
                            "Verify Sheets API is enabled for this OAuth project and token scopes include "
                            "https://www.googleapis.com/auth/spreadsheets.readonly. "
                            f"Google API detail: {sheets_detail or 'unknown'}"
                        ),
                    ) from error
        except HTTPException:
            raise
        except Exception:
            pass

        if "not found" in lowered or "permission" in lowered or "403" in lowered:
            raise HTTPException(
                status_code=403,
                detail="Unable to access Google Sheet with current account permissions.",
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


async def load_sheet_as_dataframe_with_metadata_for_user(
    db: AsyncSession,
    user: User,
    sheet_url: str,
):
    try:
        creds = _build_user_credentials(user)
        await _refresh_if_needed(db, user, creds)

        try:
            _, worksheet = _open_google_worksheet_with_credentials(sheet_url, creds)
            return _sheet_to_dataframe(worksheet), worksheet.title
        except HTTPException as error:
            # Publicly shared sheets can be fetched without OAuth via CSV export.
            # If OAuth account access is denied, we can probe public export,
            # but we must preserve the original OAuth 403 for org/private sheets.
            if error.status_code == 403:
                try:
                    return load_public_sheet_as_dataframe_with_metadata(sheet_url)
                except HTTPException as public_error:
                    if public_error.status_code == 403:
                        raise error
                    raise
            raise
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected Google Sheets OAuth error: {error}",
        ) from error


def _fetch_google_userinfo(access_token: str) -> dict:
    request = urllib.request.Request(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _drive_file_probe(access_token: str, sheet_id: str) -> tuple[bool, dict]:
    fields = "id,name,mimeType,owners(emailAddress,displayName),driveId,capabilities(canEdit,canShare)"
    drive_url = f"https://www.googleapis.com/drive/v3/files/{quote(sheet_id)}?fields={quote(fields, safe=',()')}"
    request = urllib.request.Request(
        drive_url,
        headers={"Authorization": f"Bearer {access_token}"},
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return True, payload
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = error.read().decode("utf-8").strip()
        except Exception:
            detail = str(error)
        return False, {
            "status": error.code,
            "detail": detail,
        }
    except Exception as error:
        return False, {
            "detail": str(error),
        }


def _sheets_spreadsheet_probe(access_token: str, sheet_id: str) -> tuple[bool, dict]:
    fields = "spreadsheetId,properties(title),sheets(properties(sheetId,title))"
    sheets_url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{quote(sheet_id)}"
        f"?fields={quote(fields, safe=',()')}"
    )
    request = urllib.request.Request(
        sheets_url,
        headers={"Authorization": f"Bearer {access_token}"},
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return True, payload
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = error.read().decode("utf-8").strip()
        except Exception:
            detail = str(error)
        return False, {
            "status": error.code,
            "detail": detail,
        }
    except Exception as error:
        return False, {
            "detail": str(error),
        }


async def diagnose_google_sheet_access_for_user(db: AsyncSession, user: User, sheet_url: str) -> dict:
    """
    Provide a structured diagnosis for OAuth-based sheet access problems.
    """
    creds = _build_user_credentials(user)
    await _refresh_if_needed(db, user, creds)

    if not creds.token:
        raise HTTPException(
            status_code=401,
            detail="No usable Google access token is available. Sign in with Google again.",
        )

    result: dict = {
        "sheet_url": sheet_url,
        "token_scopes": list(creds.scopes or _resolve_scopes(user)),
        "token_expires_at": user.google_oauth_token_expires_at,
        "has_refresh_token": bool(user.google_oauth_refresh_token_encrypted),
    }

    # Runtime identity tied to effective access token
    try:
        userinfo = _fetch_google_userinfo(creds.token)
        result["runtime_identity"] = {
            "google_sub": userinfo.get("sub"),
            "google_email": userinfo.get("email"),
            "google_name": userinfo.get("name"),
        }
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = error.read().decode("utf-8").strip()
        except Exception:
            detail = str(error)
        result["runtime_identity_error"] = {
            "status": error.code,
            "detail": detail,
        }
    except Exception as error:
        result["runtime_identity_error"] = {"detail": str(error)}

    # URL parsing and extracted identifiers
    sheet_id: Optional[str] = None
    requested_gid: Optional[int] = None
    try:
        sheet_id, requested_gid = _parse_sheet_url(sheet_url)
        result["parsed"] = {
            "sheet_id": sheet_id,
            "requested_gid": requested_gid,
        }
    except HTTPException as error:
        result["parse_error"] = {
            "status": error.status_code,
            "detail": error.detail,
        }
        return result

    # Drive API probe (same token)
    if sheet_id is not None:
        drive_ok, drive_payload = _drive_file_probe(creds.token, sheet_id)
        result["drive_probe"] = {
            "ok": drive_ok,
            "payload": drive_payload,
        }

        sheets_ok, sheets_payload = _sheets_spreadsheet_probe(creds.token, sheet_id)
        result["sheets_probe"] = {
            "ok": sheets_ok,
            "payload": sheets_payload,
        }

    # Sheets API probe via gspread open_by_key + worksheet resolution
    if gspread is None:
        result["gspread_probe"] = {
            "ok": False,
            "error": "gspread is not installed in backend environment",
        }
        return result

    client = gspread.authorize(creds)

    try:
        spreadsheet = client.open_by_key(sheet_id)
        worksheets = spreadsheet.worksheets()
        worksheet_summaries = [{"id": ws.id, "title": ws.title} for ws in worksheets]

        gid_found = requested_gid is None or any(ws.id == requested_gid for ws in worksheets)

        result["gspread_probe"] = {
            "ok": True,
            "spreadsheet_title": spreadsheet.title,
            "worksheet_count": len(worksheet_summaries),
            "worksheets": worksheet_summaries,
            "requested_gid_found": gid_found,
            "requested_gid": requested_gid,
        }
    except gspread.exceptions.SpreadsheetNotFound as error:
        result["gspread_probe"] = {
            "ok": False,
            "error_type": "SpreadsheetNotFound",
            "detail": _extract_error_text(error),
        }
    except gspread.exceptions.APIError as error:
        detail = _extract_error_text(error)
        probe = {
            "ok": False,
            "error_type": "APIError",
            "detail": detail,
        }
        if _is_drive_api_disabled_text(detail):
            probe["inferred_cause"] = "drive_api_disabled"
            probe["suggested_fix"] = _build_drive_api_disabled_message(detail)
        elif _is_sheets_api_disabled_text(detail):
            probe["inferred_cause"] = "sheets_api_disabled"
            probe["suggested_fix"] = _build_sheets_api_disabled_message(detail)
        elif "insufficient authentication scopes" in detail.lower() or "insufficientpermissions" in detail.lower():
            probe["inferred_cause"] = "oauth_scopes_insufficient"
            probe["suggested_fix"] = (
                "OAuth token is missing required Sheets/Drive scopes. "
                "Sign in with Google again and grant Sheets access."
            )
        result["gspread_probe"] = probe
    except Exception as error:
        detail = _extract_error_text(error)
        probe = {
            "ok": False,
            "error_type": error.__class__.__name__,
            "detail": detail,
        }

        drive_probe = result.get("drive_probe") if isinstance(result.get("drive_probe"), dict) else None
        sheets_probe = result.get("sheets_probe") if isinstance(result.get("sheets_probe"), dict) else None
        drive_ok = bool(drive_probe and drive_probe.get("ok"))
        sheets_ok = bool(sheets_probe and sheets_probe.get("ok"))

        if _is_drive_api_disabled_text(detail):
            probe["inferred_cause"] = "drive_api_disabled"
            probe["suggested_fix"] = _build_drive_api_disabled_message(detail)
        elif _is_sheets_api_disabled_text(detail):
            probe["inferred_cause"] = "sheets_api_disabled"
            probe["suggested_fix"] = _build_sheets_api_disabled_message(detail)
        elif drive_ok and not sheets_ok:
            sheets_detail = ""
            if sheets_probe and isinstance(sheets_probe.get("payload"), dict):
                sheets_detail = str(sheets_probe["payload"].get("detail", "")).strip()

            if _is_sheets_api_disabled_text(sheets_detail):
                probe["inferred_cause"] = "sheets_api_disabled"
                probe["suggested_fix"] = _build_sheets_api_disabled_message(sheets_detail)
            else:
                probe["inferred_cause"] = "sheets_api_or_scope_issue"
                probe["suggested_fix"] = (
                    "Drive access is working, but direct Sheets API probe failed. "
                    "Verify Sheets API is enabled for this OAuth project and token scopes include "
                    "https://www.googleapis.com/auth/spreadsheets.readonly."
                )
                if sheets_detail:
                    probe["sheets_probe_detail"] = sheets_detail

        result["gspread_probe"] = probe

    # Public export probe to quickly tell if this behaves like a public sheet
    try:
        _, public_sheet_name = load_public_sheet_as_dataframe_with_metadata(sheet_url)
        result["public_export_probe"] = {
            "ok": True,
            "sheet_name": public_sheet_name,
        }
    except HTTPException as error:
        result["public_export_probe"] = {
            "ok": False,
            "status": error.status_code,
            "detail": error.detail,
        }
    except Exception as error:
        result["public_export_probe"] = {
            "ok": False,
            "detail": str(error),
        }

    return result


async def get_google_oauth_runtime_identity(db: AsyncSession, user: User) -> dict:
    """
    Return the effective Google identity represented by the currently stored
    OAuth token after refresh, if needed.
    """
    creds = _build_user_credentials(user)
    await _refresh_if_needed(db, user, creds)

    if not creds.token:
        raise HTTPException(
            status_code=401,
            detail="No usable Google access token is available. Sign in with Google again.",
        )

    try:
        payload = _fetch_google_userinfo(creds.token)
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = error.read().decode("utf-8").strip()
        except Exception:
            detail = str(error)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to resolve Google identity from OAuth token. Google response: {detail}",
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to resolve Google identity from OAuth token: {error}",
        ) from error

    token_scopes = list(creds.scopes or _resolve_scopes(user))

    return {
        "google_sub": payload.get("sub"),
        "google_email": payload.get("email"),
        "google_name": payload.get("name"),
        "token_scopes": token_scopes,
        "token_expires_at": user.google_oauth_token_expires_at,
        "has_refresh_token": bool(user.google_oauth_refresh_token_encrypted),
    }
