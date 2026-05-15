import asyncio

from sqlalchemy import text

from app.db.session import engine


async def _column_exists(conn, column_name: str) -> bool:
    return bool(
        await conn.scalar(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'users'
                      AND column_name = :column_name
                )
                """
            ),
            {"column_name": column_name},
        )
    )


async def migrate() -> None:
    async with engine.begin() as conn:
        if not await _column_exists(conn, "google_oauth_access_token_encrypted"):
            await conn.execute(
                text("ALTER TABLE public.users ADD COLUMN google_oauth_access_token_encrypted TEXT NULL")
            )

        if not await _column_exists(conn, "google_oauth_refresh_token_encrypted"):
            await conn.execute(
                text("ALTER TABLE public.users ADD COLUMN google_oauth_refresh_token_encrypted TEXT NULL")
            )

        if not await _column_exists(conn, "google_oauth_token_expires_at"):
            await conn.execute(
                text("ALTER TABLE public.users ADD COLUMN google_oauth_token_expires_at TIMESTAMPTZ NULL")
            )

        if not await _column_exists(conn, "google_oauth_scopes"):
            await conn.execute(
                text("ALTER TABLE public.users ADD COLUMN google_oauth_scopes TEXT NULL")
            )

        if not await _column_exists(conn, "google_oauth_updated_at"):
            await conn.execute(
                text("ALTER TABLE public.users ADD COLUMN google_oauth_updated_at TIMESTAMPTZ NULL")
            )

    print("✅ Google OAuth token migration applied successfully")


if __name__ == "__main__":
    asyncio.run(migrate())
