import asyncio

from sqlalchemy import text

from app.db.session import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        parent_column_exists = await conn.scalar(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'messages'
                      AND column_name = 'parent_message_id'
                )
                """
            )
        )

        if not parent_column_exists:
            await conn.execute(
                text("ALTER TABLE public.messages ADD COLUMN parent_message_id UUID NULL")
            )

        fk_exists = await conn.scalar(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.table_constraints tc
                    WHERE tc.table_schema = 'public'
                      AND tc.table_name = 'messages'
                      AND tc.constraint_name = 'messages_parent_message_id_fkey'
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
                    ADD CONSTRAINT messages_parent_message_id_fkey
                    FOREIGN KEY (parent_message_id)
                    REFERENCES public.messages(id)
                    ON DELETE SET NULL
                    """
                )
            )

        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_messages_parent_message_id
                ON public.messages (parent_message_id)
                """
            )
        )

    print("✅ Message parent schema migration applied successfully")


if __name__ == "__main__":
    asyncio.run(migrate())
