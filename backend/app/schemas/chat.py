from pydantic import BaseModel
from datetime import datetime
import uuid

class ChatRequest(BaseModel):
    message: str

class MessageResponse(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime
    
    class Config:
        from_attributes = True