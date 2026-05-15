import asyncio
import json
import secrets
import urllib.parse
import urllib.request

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserLogin, UserResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _secure_cookie_enabled() -> bool:
    https_frontend = any(url.startswith("https://") for url in _frontend_origins())
    return settings.ENVIRONMENT.lower() in {"production", "staging"} or https_frontend


def _canonical_origin(url: str | None) -> str | None:
    if not url:
        return None

    try:
        parsed = urllib.parse.urlparse(url)
        if not parsed.scheme or not parsed.hostname:
            return url.rstrip("/")

        port = parsed.port
        default_port = 443 if parsed.scheme == "https" else 80
        port_suffix = f":{port}" if port and port != default_port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port_suffix}"
    except Exception:
        return url.rstrip("/")


def _is_loopback_hostname(hostname: str | None) -> bool:
    return (hostname or "").lower() in {"localhost", "127.0.0.1"}


def _is_loopback_equivalent_origin(origin_a: str, origin_b: str) -> bool:
    try:
        parsed_a = urllib.parse.urlparse(origin_a)
        parsed_b = urllib.parse.urlparse(origin_b)
        if not parsed_a.scheme or not parsed_b.scheme:
            return False
        if parsed_a.scheme != parsed_b.scheme:
            return False

        port_a = parsed_a.port or (443 if parsed_a.scheme == "https" else 80)
        port_b = parsed_b.port or (443 if parsed_b.scheme == "https" else 80)
        if port_a != port_b:
            return False

        return _is_loopback_hostname(parsed_a.hostname) and _is_loopback_hostname(parsed_b.hostname)
    except Exception:
        return False


def _frontend_origins() -> list[str]:
    candidates = []

    configured = [
        value.strip()
        for value in settings.FRONTEND_URLS.split(",")
        if value.strip()
    ]

    if settings.FRONTEND_URL:
        configured.insert(0, settings.FRONTEND_URL)

    for url in configured:
        origin = _canonical_origin(url)
        if origin and origin not in candidates:
            candidates.append(origin)

    return candidates


def _is_allowed_frontend_url(frontend_url: str | None) -> bool:
    normalized = _canonical_origin(frontend_url)
    if not normalized:
        return False

    for allowed_origin in _frontend_origins():
        if normalized == allowed_origin:
            return True
        if _is_loopback_equivalent_origin(normalized, allowed_origin):
            return True

    return False


def _resolve_frontend_url(frontend_url: str | None) -> str:
    if _is_allowed_frontend_url(frontend_url):
        return _canonical_origin(frontend_url) or settings.FRONTEND_URL.rstrip("/")

    default_origin = _canonical_origin(settings.FRONTEND_URL)
    return default_origin or settings.FRONTEND_URL.rstrip("/")


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=_secure_cookie_enabled(),
        max_age=28800,
    )


def _oauth_configured() -> bool:
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET and settings.GOOGLE_REDIRECT_URI)


def _google_oauth_scopes() -> str:
    scopes = (settings.GOOGLE_OAUTH_SCOPES or "").strip()
    return scopes or "openid email profile"


def _resolve_google_redirect_uri(request: Request) -> str:
    callback_uri = str(request.url_for("google_callback"))
    configured_uri = settings.GOOGLE_REDIRECT_URI

    # Google requires an exact redirect_uri match with the OAuth client config.
    # Always prefer the explicit configured value to avoid accidental host/port drift.
    if configured_uri:
        return configured_uri
    return callback_uri


def _exchange_code_for_token(code: str, redirect_uri: str) -> dict:
    payload = {
        "code": code,
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_google_profile(access_token: str) -> dict:
    request = urllib.request.Request(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))

@router.post("/register", response_model=UserResponse)
async def register(user_data: UserCreate, db: AsyncSession = Depends(get_db)):
    try:
        user = await AuthService.create_user(db, user_data)
        return user
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/login", response_model=UserResponse)
async def login(
    credentials: UserLogin,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    user = await AuthService.authenticate_user(db, credentials.email, credentials.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = AuthService.create_token(user)
    _set_auth_cookie(response, token)
    return user


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/google/login")
async def google_login(
    request: Request,
    frontend_url: str | None = Query(default=None),
):
    if not _oauth_configured():
        raise HTTPException(status_code=503, detail="Google OAuth is not configured")

    redirect_uri = _resolve_google_redirect_uri(request)

    # If we are on a loopback hostname different from the configured redirect URI
    # host, first hop to that host so oauth_state cookie and callback host match.
    try:
        current_host = (request.url.hostname or "").lower()
        redirect_parsed = urllib.parse.urlparse(redirect_uri)
        redirect_host = (redirect_parsed.hostname or "").lower()

        if (
            current_host != redirect_host
            and _is_loopback_hostname(current_host)
            and _is_loopback_hostname(redirect_host)
            and redirect_parsed.scheme
        ):
            default_port = 443 if redirect_parsed.scheme == "https" else 80
            port_suffix = (
                f":{redirect_parsed.port}" if redirect_parsed.port and redirect_parsed.port != default_port else ""
            )
            relay_base = f"{redirect_parsed.scheme}://{redirect_host}{port_suffix}"
            relay_query = urllib.parse.urlencode({"frontend_url": _resolve_frontend_url(frontend_url)})
            return RedirectResponse(url=f"{relay_base}{request.url.path}?{relay_query}")
    except Exception:
        # Fall through to default behavior if URL parsing fails.
        pass

    state = secrets.token_urlsafe(24)
    query = urllib.parse.urlencode(
        {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": _google_oauth_scopes(),
            "state": state,
            "prompt": "consent select_account",
            "access_type": "offline",
            "include_granted_scopes": "true",
        }
    )
    redirect = RedirectResponse(url=f"https://accounts.google.com/o/oauth2/v2/auth?{query}")
    redirect.set_cookie(
        key="oauth_state",
        value=state,
        httponly=True,
        samesite="lax",
        secure=_secure_cookie_enabled(),
        max_age=600,
    )
    resolved_frontend_url = _resolve_frontend_url(frontend_url)
    redirect.set_cookie(
        key="oauth_frontend_url",
        value=resolved_frontend_url,
        httponly=True,
        samesite="lax",
        secure=_secure_cookie_enabled(),
        max_age=600,
    )
    redirect.set_cookie(
        key="oauth_redirect_uri",
        value=redirect_uri,
        httponly=True,
        samesite="lax",
        secure=_secure_cookie_enabled(),
        max_age=600,
    )
    return redirect


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    oauth_state: str | None = Cookie(default=None),
    oauth_frontend_url: str | None = Cookie(default=None),
    oauth_redirect_uri: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
):
    origin_header = request.headers.get("origin")
    referer_header = request.headers.get("referer")
    resolved_frontend_url = _resolve_frontend_url(
        oauth_frontend_url
        if _is_allowed_frontend_url(oauth_frontend_url)
        else origin_header
        if _is_allowed_frontend_url(origin_header)
        else referer_header
        if _is_allowed_frontend_url(referer_header)
        else None
    )

    if error:
        failed = RedirectResponse(url=f"{resolved_frontend_url}/auth?error=google_oauth_failed")
        failed.delete_cookie("oauth_state")
        failed.delete_cookie("oauth_frontend_url")
        failed.delete_cookie("oauth_redirect_uri")
        return failed

    if not _oauth_configured():
        failed = RedirectResponse(url=f"{resolved_frontend_url}/auth?error=google_oauth_not_configured")
        failed.delete_cookie("oauth_state")
        failed.delete_cookie("oauth_frontend_url")
        failed.delete_cookie("oauth_redirect_uri")
        return failed

    if not code or not state or not oauth_state or state != oauth_state:
        failed = RedirectResponse(url=f"{resolved_frontend_url}/auth?error=invalid_oauth_state")
        failed.delete_cookie("oauth_state")
        failed.delete_cookie("oauth_frontend_url")
        failed.delete_cookie("oauth_redirect_uri")
        return failed

    try:
        redirect_uri = oauth_redirect_uri or _resolve_google_redirect_uri(request)
        token_payload = await asyncio.to_thread(_exchange_code_for_token, code, redirect_uri)
        access_token = token_payload.get("access_token")
        if not access_token:
            raise ValueError("No access token returned by Google")

        profile = await asyncio.to_thread(_fetch_google_profile, access_token)
        email = profile.get("email")
        google_id = profile.get("sub")
        full_name = profile.get("name")

        if not email or not google_id:
            raise ValueError("Google profile is missing required claims")

        user = await AuthService.get_or_create_google_user(
            db,
            email=email,
            google_id=google_id,
            full_name=full_name,
        )
        await AuthService.save_google_oauth_tokens(db, user, token_payload)
        token = AuthService.create_token(user)

        redirect = RedirectResponse(url=f"{resolved_frontend_url}/chat")
        _set_auth_cookie(redirect, token)
        redirect.delete_cookie("oauth_state")
        redirect.delete_cookie("oauth_frontend_url")
        redirect.delete_cookie("oauth_redirect_uri")
        return redirect
    except Exception:
        failed = RedirectResponse(url=f"{resolved_frontend_url}/auth?error=google_login_failed")
        failed.delete_cookie("oauth_state")
        failed.delete_cookie("oauth_frontend_url")
        failed.delete_cookie("oauth_redirect_uri")
        return failed

@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("access_token")
    return {"message": "Logged out successfully"}