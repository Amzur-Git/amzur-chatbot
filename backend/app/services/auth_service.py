from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.user import User
from app.schemas.user import UserCreate
from app.core.security import get_password_hash, verify_password, create_access_token
from typing import Optional
from datetime import datetime, timedelta, timezone

from app.core.token_encryption import decrypt_token, encrypt_token


def _normalize_email(email: str) -> str:
    return email.strip().lower()


class AuthService:
    @staticmethod
    def _extract_expiry(token_payload: dict) -> Optional[datetime]:
        expires_in = token_payload.get("expires_in")
        if expires_in is None:
            return None

        try:
            seconds = int(expires_in)
        except (TypeError, ValueError):
            return None

        # Refresh 60s early to avoid edge expiration races.
        return datetime.now(timezone.utc) + timedelta(seconds=max(seconds - 60, 0))

    @staticmethod
    async def create_user(db: AsyncSession, user_data: UserCreate) -> User:
        normalized_email = _normalize_email(user_data.email)

        # Check if user exists
        result = await db.execute(select(User).where(func.lower(User.email) == normalized_email))
        existing_user = result.scalar_one_or_none()
        if existing_user:
            raise ValueError("Email already registered")
        
        # Create new user
        hashed_password = get_password_hash(user_data.password)
        user = User(
            email=normalized_email,
            hashed_password=hashed_password,
            full_name=user_data.full_name
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user
    
    @staticmethod
    async def authenticate_user(db: AsyncSession, email: str, password: str) -> Optional[User]:
        normalized_email = _normalize_email(email)
        result = await db.execute(select(User).where(func.lower(User.email) == normalized_email))
        user = result.scalar_one_or_none()
        
        if not user or not user.hashed_password:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        
        return user

    @staticmethod
    async def get_or_create_google_user(
        db: AsyncSession,
        *,
        email: str,
        google_id: str,
        full_name: Optional[str],
    ) -> User:
        normalized_email = _normalize_email(email)

        # First prefer existing google_id match.
        by_google_result = await db.execute(select(User).where(User.google_id == google_id))
        user = by_google_result.scalar_one_or_none()
        if user:
            if full_name and not user.full_name:
                user.full_name = full_name
                await db.commit()
                await db.refresh(user)
            return user

        # Account linking: if email exists, attach google_id instead of creating duplicate.
        by_email_result = await db.execute(select(User).where(func.lower(User.email) == normalized_email))
        user = by_email_result.scalar_one_or_none()
        if user:
            if user.google_id and user.google_id != google_id:
                raise ValueError("Email is already linked to another Google account")

            user.google_id = google_id
            user.email = normalized_email
            if full_name and not user.full_name:
                user.full_name = full_name
            await db.commit()
            await db.refresh(user)
            return user

        # Google-only account: hashed_password stays null.
        user = User(
            email=normalized_email,
            hashed_password=None,
            google_id=google_id,
            full_name=full_name,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def save_google_oauth_tokens(
        db: AsyncSession,
        user: User,
        token_payload: dict,
    ) -> None:
        access_token = token_payload.get("access_token")
        if not access_token:
            raise ValueError("No access token present in Google OAuth payload")

        # Google may omit refresh_token for previously-consented users.
        refresh_token = token_payload.get("refresh_token")
        if not refresh_token and user.google_oauth_refresh_token_encrypted:
            refresh_token = decrypt_token(user.google_oauth_refresh_token_encrypted)

        user.google_oauth_access_token_encrypted = encrypt_token(access_token)
        user.google_oauth_refresh_token_encrypted = (
            encrypt_token(refresh_token) if refresh_token else None
        )
        user.google_oauth_token_expires_at = AuthService._extract_expiry(token_payload)

        scope = token_payload.get("scope")
        if isinstance(scope, str) and scope.strip():
            user.google_oauth_scopes = scope.strip()

        user.google_oauth_updated_at = datetime.utcnow()

        await db.commit()
        await db.refresh(user)
    
    @staticmethod
    def create_token(user: User) -> str:
        return create_access_token({"sub": user.email, "user_id": str(user.id)})