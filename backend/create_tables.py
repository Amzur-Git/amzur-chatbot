import asyncio
from sqlalchemy import text
from app.db.session import engine
from app.models.user import Base
from app.models.thread import Thread
from app.models.message import Message
from app.models.attachment import Attachment

async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_lower ON users ((lower(email)))")
        )
    print("✅ Tables created!")

if __name__ == "__main__":
    asyncio.run(create_tables())