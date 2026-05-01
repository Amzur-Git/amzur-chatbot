from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.chat import (
    ChatRequest,
    MessageResponse,
    ThreadCreateRequest,
    ThreadResponse,
    ThreadUpdateRequest,
)
from app.services.chat_service import ChatService
import uuid

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.get("/history", response_model=list[MessageResponse])
async def get_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    return await ChatService.get_history(db, current_user.id, limit=50)


@router.get("/threads", response_model=list[ThreadResponse])
async def get_threads(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ChatService.list_threads(db, current_user.id)


@router.post("/threads", response_model=ThreadResponse)
async def create_thread(
    request: ThreadCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ChatService.create_thread(db, current_user.id, request.title)


@router.patch("/threads/{thread_id}", response_model=ThreadResponse)
async def update_thread(
    thread_id: uuid.UUID,
    request: ThreadUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    thread = await ChatService.get_thread(db, current_user.id, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    try:
        return await ChatService.rename_thread(db, thread, request.title)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.delete("/threads/{thread_id}")
async def delete_thread(
    thread_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    thread = await ChatService.get_thread(db, current_user.id, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    await ChatService.delete_thread(db, thread)
    return {"message": "Thread deleted"}


@router.get("/threads/{thread_id}/messages", response_model=list[MessageResponse])
async def get_thread_messages(
    thread_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    thread = await ChatService.get_thread(db, current_user.id, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    return await ChatService.get_thread_history(db, current_user.id, thread_id, limit=100)


@router.post("/threads/{thread_id}/send", response_model=MessageResponse)
async def send_thread_message(
    thread_id: uuid.UUID,
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    thread = await ChatService.get_thread(db, current_user.id, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    # Save user message in this thread.
    await ChatService.save_message(db, current_user.id, "user", request.message, thread_id=thread.id)
    await ChatService.ensure_thread_title(db, thread, request.message)

    # Generate assistant response from thread-local history.
    history = await ChatService.get_thread_history(db, current_user.id, thread.id, limit=10)
    ai_response = await ChatService.generate_response(current_user.email, request.message, history)

    # Save assistant response.
    message = await ChatService.save_message(
        db,
        current_user.id,
        "assistant",
        ai_response,
        thread_id=thread.id,
    )
    return message

@router.post("/send", response_model=MessageResponse)
async def send_message(
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # Legacy endpoint compatibility: send into most recent active thread,
    # creating one when user has none.
    threads = await ChatService.list_threads(db, current_user.id)
    thread = threads[0] if threads else await ChatService.create_thread(db, current_user.id)
    return await send_thread_message(thread.id, request, current_user, db)