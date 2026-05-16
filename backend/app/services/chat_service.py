from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import selectinload
from fastapi import HTTPException
from app.ai.rag import PdfRagService
from app.models.attachment import Attachment
from app.models.message import Message
from app.models.thread import Thread
from app.ai.llm import client
from app.core.config import settings
from app.services.attachment_service import AttachmentService
from app.services.db_qa_service import DbQaService
from app.services.sheets_query_service import query_dataframe_with_langchain
from app.services.sheets_service import load_file_as_dataframe
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional


DEFAULT_THREAD_TITLE = "New chat"
logger = logging.getLogger(__name__)

class ChatService:
    MEMORY_TURNS = 5
    MAX_VISUAL_INPUTS = 6

    @staticmethod
    def _is_missing_attachments_table_error(error: Exception) -> bool:
        return 'relation "attachments" does not exist' in str(error)

    @staticmethod
    async def get_history(db: AsyncSession, user_id: uuid.UUID, limit: int = 10):
        query = (
            select(Message)
            .where(Message.user_id == user_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )

        try:
            result = await db.execute(query.options(selectinload(Message.attachments)))
            return list(reversed(result.scalars().all()))
        except ProgrammingError as error:
            if not ChatService._is_missing_attachments_table_error(error):
                raise

            await db.rollback()
            result = await db.execute(query)
            messages = list(reversed(result.scalars().all()))
            for message in messages:
                message.attachments = []
            return messages

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
        try:
            attachments_result = await db.execute(
                select(Attachment).where(Attachment.thread_id == thread.id)
            )
            for attachment in attachments_result.scalars().all():
                await AttachmentService.delete(db, attachment, auto_commit=False)
        except ProgrammingError as error:
            if not ChatService._is_missing_attachments_table_error(error):
                raise
            await db.rollback()

        thread.is_deleted = True
        await db.commit()

    @staticmethod
    async def get_thread_history(
        db: AsyncSession,
        user_id: uuid.UUID,
        thread_id: uuid.UUID,
        limit: int = 50,
    ):
        query = (
            select(Message)
            .where(
                Message.user_id == user_id,
                Message.thread_id == thread_id,
            )
            .order_by(Message.created_at.desc())
            .limit(limit)
        )

        try:
            result = await db.execute(query.options(selectinload(Message.attachments)))
            return list(reversed(result.scalars().all()))
        except ProgrammingError as error:
            if not ChatService._is_missing_attachments_table_error(error):
                raise

            await db.rollback()
            result = await db.execute(query)
            messages = list(reversed(result.scalars().all()))
            for message in messages:
                message.attachments = []
            return messages

    @staticmethod
    async def get_thread_memory(
        db: AsyncSession,
        user_id: uuid.UUID,
        thread_id: uuid.UUID,
        turns: int | None = None,
    ):
        # One conversation turn is user + assistant, so we keep 2 messages per turn.
        # We fetch one extra record because the current user prompt is already persisted
        # before response generation and is excluded below.
        max_turns = turns or ChatService.MEMORY_TURNS
        history = await ChatService.get_thread_history(
            db,
            user_id,
            thread_id,
            limit=(max_turns * 2) + 1,
        )

        # Exclude the newest user message because generate_response appends user_message.
        if history and history[-1].role == "user":
            return history[:-1]

        return history

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
    def _map_llm_error(error: Exception) -> HTTPException:
        text = str(error).lower()

        if (
            "quota" in text
            or "rate limit" in text
            or "resource_exhausted" in text
            or "resourceexhausted" in text
            or "429" in text
            or "too many requests" in text
        ):
            return HTTPException(
                status_code=429,
                detail="LLM quota or rate limit reached via LiteLLM/provider. Please try again later",
            )

        if "api key" in text or "permission" in text or "unauthorized" in text or "401" in text:
            return HTTPException(
                status_code=401,
                detail="LiteLLM/provider authentication failed for chat model",
            )

        if ("not found" in text and "model" in text) or "does not exist" in text:
            return HTTPException(
                status_code=502,
                detail=(
                    "Configured chat model is unavailable on LiteLLM/provider. "
                    "Update LLM_MODEL to a valid model"
                ),
            )

        if "timeout" in text:
            return HTTPException(
                status_code=504,
                detail="LLM request timed out",
            )

        if "safety" in text or "blocked" in text or "content policy" in text:
            return HTTPException(
                status_code=400,
                detail="LLM request blocked by provider safety filters",
            )

        return HTTPException(
            status_code=502,
            detail="LLM request failed via LiteLLM/provider",
        )

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

        # Keep thread ordering deterministic after refreshes by bumping recency
        # whenever a message is added to an existing thread.
        if thread_id:
            thread = await ChatService.get_thread(db, user_id, thread_id)
            if thread:
                thread.updated_at = datetime.utcnow()

        await db.commit()
        await db.refresh(message)
        return message
    
    @staticmethod
    def _answer_table_attachment_question(
        user_message: str,
        attachments: list[Attachment] | None,
    ) -> str:
        if not attachments:
            return ""

        table_attachments = [attachment for attachment in attachments if attachment.file_type == "table"]
        if not table_attachments:
            return ""

        # Prefer the latest attached table to match user intent in multi-file chats.
        table_attachments.sort(key=lambda item: item.created_at, reverse=True)

        for attachment in table_attachments:
            try:
                dataframe = load_file_as_dataframe(attachment.file_path)
                result = query_dataframe_with_langchain(dataframe, user_message)
                answer = str(result.get("answer", "")).strip()
                if answer:
                    return answer
            except Exception:
                logger.exception(
                    "Table attachment query failed for attachment=%s file=%s",
                    attachment.id,
                    attachment.file_name,
                )

        return ""

    @staticmethod
    async def generate_response(
        db: AsyncSession,
        user_email: str,
        user_message: str,
        history: list,
        attachments: list[Attachment] | None = None,
        user_id: uuid.UUID | None = None,
        thread_id: uuid.UUID | None = None,
        db_query_mode: bool = False,
    ):
        if not db_query_mode:
            table_answer = ChatService._answer_table_attachment_question(user_message, attachments)
            if table_answer:
                return table_answer

        db_answer = await DbQaService.answer_question(db, user_message, db_query_mode=db_query_mode)
        if db_answer:
            return db_answer

        messages = [{"role": msg.role, "content": msg.content} for msg in history]
        attachment_context = AttachmentService.build_ai_context(attachments or [])
        rag_context = ""

        if user_id and thread_id:
            try:
                rag_context = PdfRagService.build_context(
                    user_id=user_id,
                    thread_id=thread_id,
                    query=user_message,
                    top_k=settings.PDF_RAG_TOP_K,
                    max_chars=settings.PDF_RAG_MAX_CONTEXT_CHARS,
                )
            except Exception:
                logger.exception(
                    "PDF retrieval failed for thread=%s user=%s",
                    thread_id,
                    user_id,
                )

        content = user_message
        if rag_context:
            content = f"{content}\n\n{rag_context}"
        if attachment_context:
            max_chars = settings.MAX_ATTACHMENT_CONTEXT_CHARS
            content = f"{content}\n\n{attachment_context[:max_chars]}"

        visual_inputs = AttachmentService.collect_visual_data_urls(
            attachments or [],
            max_items=ChatService.MAX_VISUAL_INPUTS,
        )

        if visual_inputs:
            grounded_instruction = (
                "You are given image frames extracted from uploaded attachments. "
                "Analyze the visuals directly and identify specific scenes, objects, places, "
                "or natural phenomena when visible (for example: aurora borealis, skyline, "
                "mountains, maps on phone screens). "
                "Do not fallback to generic boilerplate if distinctive visual evidence exists. "
                "If uncertain, state uncertainty clearly."
            )
            user_content: list[dict[str, object]] = [
                {
                    "type": "text",
                    "text": f"{grounded_instruction}\n\nUser request:\n{content}",
                }
            ]
            for data_url in visual_inputs:
                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    }
                )
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": content})

        try:
            response = client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=messages,
                user=user_email,
                extra_body={"metadata": {"application": settings.APP_NAME}},
            )
            return response.choices[0].message.content
        except HTTPException:
            raise
        except Exception as error:
            logger.exception(
                "Chat completion failed: model=%s user=%s",
                settings.LLM_MODEL,
                user_email,
            )
            raise ChatService._map_llm_error(error) from error