from pydantic import BaseModel, Field
from datetime import datetime
import uuid
from typing import Optional

from app.schemas.attachment import AttachmentMetadataResponse

class ChatRequest(BaseModel):
    message: str
    attachment_ids: list[uuid.UUID] = Field(default_factory=list)
    formula_text: Optional[str] = None
    db_query_mode: bool = False
    num_images: Optional[int] = Field(default=None, ge=1, le=4)
    aspect_ratio: Optional[str] = None
    negative_prompt: Optional[str] = None
    enhance_prompt: Optional[bool] = True


class ThreadCreateRequest(BaseModel):
    title: Optional[str] = None


class ThreadUpdateRequest(BaseModel):
    title: str


class ThreadResponse(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class MessageResponse(BaseModel):
    id: uuid.UUID
    thread_id: Optional[uuid.UUID]
    role: str
    content: str
    attachments: list[AttachmentMetadataResponse] = Field(default_factory=list)
    created_at: datetime
    
    class Config:
        from_attributes = True