import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.attachment import (
    AttachmentDeleteResponse,
    AttachmentMetadataResponse,
    AttachmentUploadResponse,
)
from app.services.attachment_service import AttachmentService


router = APIRouter(prefix="/api/attachments", tags=["attachments"])


@router.post("/upload", response_model=AttachmentUploadResponse)
async def upload_attachment(
    thread_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    attachment = await AttachmentService.upload(db, current_user, thread_id, file)
    return AttachmentService.to_response(attachment)


@router.get("/{attachment_id}", response_model=AttachmentMetadataResponse)
async def get_attachment_metadata(
    attachment_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    attachment = await AttachmentService.get_by_id(db, current_user, attachment_id)
    return AttachmentMetadataResponse(
        id=attachment.id,
        message_id=attachment.message_id,
        thread_id=attachment.thread_id,
        file_type=attachment.file_type,
        file_name=attachment.file_name,
        file_size=attachment.file_size,
        metadata=attachment.metadata_json or {},
        created_at=attachment.created_at,
    )


@router.get("/{attachment_id}/download")
async def download_attachment(
    attachment_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    attachment = await AttachmentService.get_by_id(db, current_user, attachment_id)
    if not Path(attachment.file_path).exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=attachment.file_path, filename=attachment.file_name)


@router.delete("/{attachment_id}", response_model=AttachmentDeleteResponse)
async def delete_attachment(
    attachment_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    attachment = await AttachmentService.get_by_id(db, current_user, attachment_id)
    await AttachmentService.delete(db, attachment)
    return AttachmentDeleteResponse(message="Attachment deleted")
