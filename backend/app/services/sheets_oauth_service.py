from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

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
            # If OAuth account access is denied, try a public export fallback.
            if error.status_code == 403:
                return load_public_sheet_as_dataframe_with_metadata(sheet_url)
            raise
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected Google Sheets OAuth error: {error}",
        ) from error
