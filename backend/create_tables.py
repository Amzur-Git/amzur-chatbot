import asyncio
from app.db.session import engine
from app.models.user import Base
from app.models.thread import Thread
from app.models.message import Message  # ADD THIS

async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Tables created!")

if __name__ == "__main__":
    asyncio.run(create_tables())