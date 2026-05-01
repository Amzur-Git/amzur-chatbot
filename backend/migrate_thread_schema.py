import asyncio

from sqlalchemy import text

from app.db.session import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        # 1) Ensure threads table exists.
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.threads (
                    id UUID PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES public.users(id),
                    title VARCHAR(255) NOT NULL DEFAULT 'New chat',
                    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ
                )
                """
            )
        )

        # 2) Ensure expected threads indexes exist.
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_threads_user_id
                ON public.threads (user_id)
                """
            )
        )

        # 3) Add messages.thread_id if missing.
        thread_id_exists = await conn.scalar(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'messages'
                      AND column_name = 'thread_id'
                )
                """
            )
        )

        if not thread_id_exists:
            await conn.execute(
                text("ALTER TABLE public.messages ADD COLUMN thread_id UUID NULL")
            )

        # 4) Ensure FK exists from messages.thread_id to threads.id.
        fk_exists = await conn.scalar(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.table_constraints tc
                    WHERE tc.table_schema = 'public'
                      AND tc.table_name = 'messages'
                      AND tc.constraint_name = 'messages_thread_id_fkey'
                      AND tc.constraint_type = 'FOREIGN KEY'
                )
                """
            )
        )

        if not fk_exists:
            await conn.execute(
                text(
                    """
                    ALTER TABLE public.messages
                    ADD CONSTRAINT messages_thread_id_fkey
                    FOREIGN KEY (thread_id) REFERENCES public.threads(id)
                    """
                )
            )

        # 5) Ensure index for thread-scoped message queries.
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_messages_thread_id
                ON public.messages (thread_id)
                """
            )
        )

    print("✅ Thread schema migration applied successfully")


if __name__ == "__main__":
    asyncio.run(migrate())
