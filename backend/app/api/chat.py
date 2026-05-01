from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.chat import ChatRequest, MessageResponse
from app.services.chat_service import ChatService
import uuid

router = APIRouter(prefix="/api/chat", tags=["chat"])

@router.post("/send", response_model=MessageResponse)
async def send_message(
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # Save user message
    await ChatService.save_message(db, current_user.id, "user", request.message)
    
    # Get history
    history = await ChatService.get_history(db, current_user.id, limit=10)
    
    # Generate response
    ai_response = await ChatService.generate_response(
        current_user.email,
        request.message,
        history
    )
    
    # Save AI response
    message = await ChatService.save_message(db, current_user.id, "assistant", ai_response)
    return message