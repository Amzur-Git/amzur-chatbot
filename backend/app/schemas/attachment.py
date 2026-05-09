from datetime import datetime
import uuid
from typing import Any

from pydantic import BaseModel, Field


class AttachmentMetadataResponse(BaseModel):
    id: uuid.UUID
    message_id: uuid.UUID | None
    thread_id: uuid.UUID
    file_type: str
    file_name: str
    file_size: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    class Config:
        from_attributes = True


class AttachmentUploadResponse(AttachmentMetadataResponse):
    download_url: str


class AttachmentDeleteResponse(BaseModel):
    message: str
