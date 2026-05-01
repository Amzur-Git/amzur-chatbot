from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from app.models.message import Message
from app.models.thread import Thread
from app.ai.llm import client
from app.core.config import settings
import asyncio
import uuid
from typing import Optional


DEFAULT_THREAD_TITLE = "New chat"

class ChatService:
    @staticmethod
    async def get_history(db: AsyncSession, user_id: uuid.UUID, limit: int = 10):
        result = await db.execute(
            select(Message)
            .where(Message.user_id == user_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    @staticmethod
    async def list_threads(db: AsyncSession, user_id: uuid.UUID):
        result = await db.execute(
            select(Thread)
            .where(Thread.user_id == user_id, Thread.is_deleted.is_(False))
            .order_by(Thread.updated_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def create_thread(db: AsyncSession, user_id: uuid.UUID, title: Optional[str] = None):
        thread_title = (title or "").strip() or DEFAULT_THREAD_TITLE
        thread = Thread(user_id=user_id, title=thread_title)
        db.add(thread)
        await db.commit()
        await db.refresh(thread)
        return thread

    @staticmethod
    async def get_thread(db: AsyncSession, user_id: uuid.UUID, thread_id: uuid.UUID):
        result = await db.execute(
            select(Thread).where(
                Thread.id == thread_id,
                Thread.user_id == user_id,
                Thread.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def rename_thread(db: AsyncSession, thread: Thread, title: str):
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("Thread title cannot be empty")

        thread.title = normalized_title
        await db.commit()
        await db.refresh(thread)
        return thread

    @staticmethod
    async def delete_thread(db: AsyncSession, thread: Thread):
        thread.is_deleted = True
        await db.commit()

    @staticmethod
    async def get_thread_history(
        db: AsyncSession,
        user_id: uuid.UUID,
        thread_id: uuid.UUID,
        limit: int = 50,
    ):
        result = await db.execute(
            select(Message)
            .where(
                Message.user_id == user_id,
                Message.thread_id == thread_id,
            )
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    @staticmethod
    async def _count_user_messages_in_thread(db: AsyncSession, thread_id: uuid.UUID):
        result = await db.execute(
            select(func.count(Message.id)).where(
                Message.thread_id == thread_id,
                Message.role == "user",
            )
        )
        return result.scalar_one()

    @staticmethod
    def _fallback_thread_title(user_message: str):
        tokens = user_message.replace("\n", " ").strip().split()
        if not tokens:
            return DEFAULT_THREAD_TITLE
        return " ".join(tokens[:6])

    @staticmethod
    def _generate_thread_title_with_llm(user_message: str):
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Generate a concise chat title of at most 6 words. "
                        "Return only the title without punctuation at the end."
                    ),
                },
                {"role": "user", "content": user_message[:500]},
            ],
            extra_body={"metadata": {"application": settings.APP_NAME, "purpose": "thread-title"}},
        )
        title = (response.choices[0].message.content or "").strip()
        if not title:
            return None
        return title[:255]

    @staticmethod
    async def ensure_thread_title(db: AsyncSession, thread: Thread, user_message: str):
        if thread.title and thread.title != DEFAULT_THREAD_TITLE:
            return

        user_message_count = await ChatService._count_user_messages_in_thread(db, thread.id)
        if user_message_count != 1:
            return

        generated_title = None
        try:
            generated_title = await asyncio.to_thread(
                ChatService._generate_thread_title_with_llm,
                user_message,
            )
        except Exception:
            generated_title = None

        thread.title = (generated_title or ChatService._fallback_thread_title(user_message)).strip()[:255]
        await db.commit()
        await db.refresh(thread)
    
    @staticmethod
    async def save_message(
        db: AsyncSession,
        user_id: uuid.UUID,
        role: str,
        content: str,
        thread_id: Optional[uuid.UUID] = None,
    ):
        message = Message(user_id=user_id, thread_id=thread_id, role=role, content=content)
        db.add(message)
        await db.commit()
        await db.refresh(message)
        return message
    
    @staticmethod
    async def generate_response(user_email: str, user_message: str, history: list):
        messages = [{"role": msg.role, "content": msg.content} for msg in history]
        messages.append({"role": "user", "content": user_message})
        
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=messages,
            user=user_email,
            extra_body={"metadata": {"application": settings.APP_NAME}}
        )
        return response.choices[0].message.content