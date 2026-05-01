from pydantic import BaseModel
from datetime import datetime
import uuid
from typing import Optional

class ChatRequest(BaseModel):
    message: str


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
    created_at: datetime
    
    class Config:
        from_attributes = True