import asyncio

from sqlalchemy import text

from app.db.session import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.workflow_runs (
                    id UUID PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES public.users(id),
                    thread_id UUID NOT NULL REFERENCES public.threads(id),
                    user_request TEXT NOT NULL,
                    topic VARCHAR(300) NOT NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'running',
                    step_results JSON NOT NULL DEFAULT '{}'::json,
                    error_messages JSON NOT NULL DEFAULT '[]'::json,
                    digest_text TEXT NULL,
                    digest_message_id UUID NULL,
                    image_prompt TEXT NULL,
                    image_message_id UUID NULL,
                    image_attachment_id UUID NULL,
                    slack_delivery_status VARCHAR(32) NULL,
                    started_at TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ NULL,
                    created_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ
                )
                """
            )
        )

        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_workflow_runs_user_id
                ON public.workflow_runs (user_id)
                """
            )
        )

        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_workflow_runs_thread_id
                ON public.workflow_runs (thread_id)
                """
            )
        )

    print("✅ Workflow runs migration applied successfully")


if __name__ == "__main__":
    asyncio.run(migrate())
