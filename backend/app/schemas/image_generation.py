from datetime import datetime
import uuid
from typing import Any

from pydantic import BaseModel, Field


class ImageGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)
    chat_thread_id: uuid.UUID
    num_images: int = Field(default=1, ge=1, le=4)
    aspect_ratio: str | None = None
    negative_prompt: str | None = None
    enhance_prompt: bool = True


class GeneratedImageItem(BaseModel):
    url: str
    attachment_id: uuid.UUID
    metadata: dict[str, Any] = Field(default_factory=dict)


class ImageGenerateResponse(BaseModel):
    images: list[GeneratedImageItem] = Field(default_factory=list)
    generated_at: datetime
