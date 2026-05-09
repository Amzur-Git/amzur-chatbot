from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import uuid
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attachment import Attachment
from app.models.message import Message
from app.models.user import User
from app.schemas.attachment import AttachmentUploadResponse
from app.services.attachments.processors import FormulaProcessor, process_file
from app.services.attachments.storage import make_storage_path
from app.services.attachments.validator import validate_upload


class AttachmentService:
    @staticmethod
    async def upload(
        db: AsyncSession,
        user: User,
        thread_id: uuid.UUID,
        upload_file: UploadFile,
    ) -> Attachment:
        file_type, file_size = await validate_upload(upload_file)

        payload = await upload_file.read()
        storage_path = make_storage_path(file_type, upload_file.filename or "file")
        storage_path.write_bytes(payload)

        try:
            metadata = process_file(file_type, storage_path)
        except Exception as error:
            storage_path.unlink(missing_ok=True)
            if isinstance(error, HTTPException):
                raise
            raise HTTPException(status_code=400, detail=f"Failed to process file: {error}") from error

        attachment = Attachment(
            thread_id=thread_id,
            user_id=user.id,
            file_type=file_type,
            file_name=upload_file.filename or storage_path.name,
            file_path=str(storage_path),
            file_size=file_size,
            metadata_json=metadata,
        )
        db.add(attachment)
        await db.commit()
        await db.refresh(attachment)
        return attachment

    @staticmethod
    async def get_by_id(db: AsyncSession, user: User, attachment_id: uuid.UUID) -> Attachment:
        result = await db.execute(
            select(Attachment).where(
                Attachment.id == attachment_id,
                Attachment.user_id == user.id,
            )
        )
        attachment = result.scalar_one_or_none()
        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")
        return attachment

    @staticmethod
    async def delete(db: AsyncSession, attachment: Attachment, auto_commit: bool = True) -> None:
        path = Path(attachment.file_path)
        path.unlink(missing_ok=True)
        await db.delete(attachment)
        if auto_commit:
            await db.commit()

    @staticmethod
    async def attach_to_message(
        db: AsyncSession,
        user: User,
        thread_id: uuid.UUID,
        message_id: uuid.UUID,
        attachment_ids: list[uuid.UUID] | None,
    ) -> list[Attachment]:
        if not attachment_ids:
            return []

        result = await db.execute(
            select(Attachment).where(
                Attachment.id.in_(attachment_ids),
                Attachment.user_id == user.id,
                Attachment.thread_id == thread_id,
            )
        )
        attachments = list(result.scalars().all())
        found_ids = {item.id for item in attachments}
        missing = [str(item_id) for item_id in attachment_ids if item_id not in found_ids]
        if missing:
            raise HTTPException(status_code=400, detail=f"Invalid attachment ids: {', '.join(missing)}")

        for attachment in attachments:
            attachment.message_id = message_id

        await db.commit()
        return attachments

    @staticmethod
    def to_response(attachment: Attachment) -> AttachmentUploadResponse:
        return AttachmentUploadResponse(
            id=attachment.id,
            message_id=attachment.message_id,
            thread_id=attachment.thread_id,
            file_type=attachment.file_type,
            file_name=attachment.file_name,
            file_size=attachment.file_size,
            metadata=attachment.metadata_json or {},
            created_at=attachment.created_at,
            download_url=f"/api/attachments/{attachment.id}/download",
        )

    @staticmethod
    def build_ai_context(attachments: list[Attachment]) -> str:
        if not attachments:
            return ""

        blocks: list[str] = ["Attachments context:"]
        for index, attachment in enumerate(attachments, start=1):
            metadata: dict[str, Any] = attachment.metadata_json or {}
            clean_metadata = {key: value for key, value in metadata.items() if key not in {"content", "records"}}
            blocks.append(
                f"{index}. [{attachment.file_type}] {attachment.file_name} (size={attachment.file_size} bytes)"
            )
            blocks.append(f"   metadata={clean_metadata}")

            if attachment.file_type == "code" and metadata.get("content"):
                code_content = str(metadata["content"])[:12000]
                blocks.append(f"   code_content:\n{code_content}")
            elif attachment.file_type == "table" and metadata.get("records"):
                blocks.append(f"   table_records={metadata['records']}")
            elif attachment.file_type == "formula" and metadata.get("latex"):
                blocks.append(f"   latex={metadata['latex']}")
            elif attachment.file_type == "image" and metadata.get("base64_preview"):
                blocks.append("   image_base64_preview=<truncated>")

        return "\n".join(blocks)

    @staticmethod
    async def create_formula_attachment(
        db: AsyncSession,
        user: User,
        thread_id: uuid.UUID,
        latex: str,
    ) -> Attachment:
        metadata = FormulaProcessor.from_text(latex)
        storage_path = make_storage_path("formula", "formula.tex")
        storage_path.write_text(latex, encoding="utf-8")
        attachment = Attachment(
            thread_id=thread_id,
            user_id=user.id,
            file_type="formula",
            file_name="formula.tex",
            file_path=str(storage_path),
            file_size=len(latex.encode("utf-8")),
            metadata_json=metadata,
        )
        db.add(attachment)
        await db.commit()
        await db.refresh(attachment)
        return attachment

    @staticmethod
    async def create_generated_image_attachment(
        db: AsyncSession,
        user: User,
        thread_id: uuid.UUID,
        bytes_data: bytes,
        mime_type: str,
        prompt: str,
        model_version: str | None = None,
        aspect_ratio: str | None = None,
        auto_commit: bool = True,
    ) -> Attachment:
        extension = "png"
        if "/" in (mime_type or ""):
            extension = mime_type.split("/")[-1].strip().lower() or "png"

        storage_name = f"generated.{extension}"
        storage_path = make_storage_path("image", storage_name)
        storage_path.write_bytes(bytes_data)

        metadata = {
            "generated": True,
            "source": "gemini",
            "prompt": prompt,
            "model_version": model_version,
            "aspect_ratio": aspect_ratio,
            "mime_type": mime_type,
        }

        attachment = Attachment(
            thread_id=thread_id,
            user_id=user.id,
            file_type="image",
            file_name=storage_name,
            file_path=str(storage_path),
            file_size=len(bytes_data),
            metadata_json=metadata,
        )
        db.add(attachment)
        if auto_commit:
            await db.commit()
            await db.refresh(attachment)
        return attachment

    @staticmethod
    async def get_latest_generated_prompt(
        db: AsyncSession,
        user: User,
        thread_id: uuid.UUID,
    ) -> str | None:
        result = await db.execute(
            select(Attachment)
            .where(
                Attachment.user_id == user.id,
                Attachment.thread_id == thread_id,
                Attachment.file_type == "image",
            )
            .order_by(Attachment.created_at.desc())
            .limit(20)
        )
        attachments = list(result.scalars().all())

        for attachment in attachments:
            metadata = attachment.metadata_json or {}
            if metadata.get("generated") and metadata.get("prompt"):
                return str(metadata["prompt"])
        return None

    @staticmethod
    async def cleanup_expired_generated_images(
        db: AsyncSession,
        user: User,
        retention_days: int,
    ) -> int:
        if retention_days <= 0:
            return 0

        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        result = await db.execute(
            select(Attachment).where(
                Attachment.user_id == user.id,
                Attachment.file_type == "image",
                Attachment.created_at < cutoff,
            )
        )
        attachments = list(result.scalars().all())

        deleted = 0
        for attachment in attachments:
            metadata = attachment.metadata_json or {}
            if not metadata.get("generated"):
                continue
            await AttachmentService.delete(db, attachment, auto_commit=False)
            deleted += 1

        if deleted:
            await db.commit()
        return deleted
