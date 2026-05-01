from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.user import User
from app.schemas.user import UserCreate
from app.core.security import get_password_hash, verify_password, create_access_token
from typing import Optional

class AuthService:
    @staticmethod
    async def create_user(db: AsyncSession, user_data: UserCreate) -> User:
        # Check if user exists
        result = await db.execute(select(User).where(User.email == user_data.email))
        existing_user = result.scalar_one_or_none()
        if existing_user:
            raise ValueError("Email already registered")
        
        # Create new user
        hashed_password = get_password_hash(user_data.password)
        user = User(
            email=user_data.email,
            hashed_password=hashed_password,
            full_name=user_data.full_name
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user
    
    @staticmethod
    async def authenticate_user(db: AsyncSession, email: str, password: str) -> Optional[User]:
        result = await db.execute(select(User).where(User.email == email))
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
        by_email_result = await db.execute(select(User).where(User.email == email))
        user = by_email_result.scalar_one_or_none()
        if user:
            user.google_id = google_id
            if full_name and not user.full_name:
                user.full_name = full_name
            await db.commit()
            await db.refresh(user)
            return user

        # Google-only account: hashed_password stays null.
        user = User(
            email=email,
            hashed_password=None,
            google_id=google_id,
            full_name=full_name,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user
    
    @staticmethod
    def create_token(user: User) -> str:
        return create_access_token({"sub": user.email, "user_id": str(user.id)})