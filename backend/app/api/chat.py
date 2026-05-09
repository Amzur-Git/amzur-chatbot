from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
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
from app.schemas.attachment import AttachmentMetadataResponse
from app.services.attachment_service import AttachmentService
from app.services.chat_service import ChatService
from app.services.image_generation_service import ImageGenerationService
from app.services.image_intent_service import ImageIntentService
import uuid

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _to_attachment_metadata(items) -> list[AttachmentMetadataResponse]:
    payload: list[AttachmentMetadataResponse] = []
    for attachment in items or []:
        payload.append(
            AttachmentMetadataResponse(
                id=attachment.id,
                message_id=attachment.message_id,
                thread_id=attachment.thread_id,
                file_type=attachment.file_type,
                file_name=attachment.file_name,
                file_size=attachment.file_size,
                metadata=attachment.metadata_json or {},
                created_at=attachment.created_at,
            )
        )
    return payload


def _to_message_response(message) -> MessageResponse:
    # Avoid triggering lazy loads in sync serialization paths.
    attachments = message.__dict__.get("attachments", [])
    return MessageResponse(
        id=message.id,
        thread_id=message.thread_id,
        role=message.role,
        content=message.content,
        attachments=_to_attachment_metadata(attachments),
        created_at=message.created_at,
    )


@router.get("/history", response_model=list[MessageResponse])
async def get_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    history = await ChatService.get_history(db, current_user.id, limit=50)
    return [_to_message_response(message) for message in history]


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

    history = await ChatService.get_thread_history(db, current_user.id, thread_id, limit=100)
    return [_to_message_response(message) for message in history]


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

    formula_attachment_ids: list[uuid.UUID] = []
    if request.formula_text:
        formula = await AttachmentService.create_formula_attachment(
            db,
            current_user,
            thread.id,
            request.formula_text,
        )
        formula_attachment_ids.append(formula.id)

    user_message = await ChatService.save_message(
        db,
        current_user.id,
        "user",
        request.message,
        thread_id=thread.id,
    )

    attachment_ids = list(request.attachment_ids or []) + formula_attachment_ids
    linked_attachments = await AttachmentService.attach_to_message(
        db,
        current_user,
        thread.id,
        user_message.id,
        attachment_ids,
    )

    await ChatService.ensure_thread_title(db, thread, request.message)

    recent_generated_prompt = await AttachmentService.get_latest_generated_prompt(
        db,
        current_user,
        thread.id,
    )
    image_intent = ImageIntentService.detect(
        request.message,
        recent_generated_prompt=recent_generated_prompt,
        default_aspect_ratio=settings.IMAGE_GEN_DEFAULT_ASPECT_RATIO,
    )

    if request.num_images is not None:
        image_intent.num_images = request.num_images
    if request.aspect_ratio:
        image_intent.aspect_ratio = request.aspect_ratio
    if request.negative_prompt:
        image_intent.negative_prompt = request.negative_prompt

    if image_intent.triggered:
        generated_items = await ImageGenerationService.generate_images(
            user_key=str(current_user.id),
            prompt=image_intent.prompt,
            num_images=image_intent.num_images,
            aspect_ratio=image_intent.aspect_ratio or settings.IMAGE_GEN_DEFAULT_ASPECT_RATIO,
            negative_prompt=image_intent.negative_prompt,
            enhance_prompt=bool(request.enhance_prompt),
        )

        summary = f"Generated {len(generated_items)} image(s) for: {request.message.strip()}"
        message = await ChatService.save_message(
            db,
            current_user.id,
            "assistant",
            summary,
            thread_id=thread.id,
        )

        generated_attachments = []
        for generated in generated_items:
            attachment = await AttachmentService.create_generated_image_attachment(
                db,
                current_user,
                thread.id,
                generated.bytes_data,
                generated.mime_type,
                prompt=generated.prompt_used,
                model_version=generated.model_version,
                aspect_ratio=image_intent.aspect_ratio,
                auto_commit=False,
            )
            attachment.message_id = message.id
            generated_attachments.append(attachment)

        if generated_attachments:
            await db.commit()
            for attachment in generated_attachments:
                await db.refresh(attachment)

        # Avoid lazy-load during serialization.
        message.__dict__["attachments"] = generated_attachments

        try:
            await AttachmentService.cleanup_expired_generated_images(
                db,
                current_user,
                settings.IMAGE_RETENTION_DAYS,
            )
        except Exception:
            # Best-effort cleanup should not block request success.
            await db.rollback()

        return _to_message_response(message)

    # Generate assistant response from the previous 5 thread-local turns.
    history = await ChatService.get_thread_memory(db, current_user.id, thread.id)
    ai_response = await ChatService.generate_response(
        current_user.email,
        request.message,
        history,
        attachments=linked_attachments,
    )

    # Save assistant response.
    message = await ChatService.save_message(
        db,
        current_user.id,
        "assistant",
        ai_response,
        thread_id=thread.id,
    )
    return _to_message_response(message)

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