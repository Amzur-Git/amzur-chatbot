from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.message import Message
from app.models.user import User
from app.ai.llm import client
from app.core.config import settings
import uuid

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
    async def save_message(db: AsyncSession, user_id: uuid.UUID, role: str, content: str):
        message = Message(user_id=user_id, role=role, content=content)
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