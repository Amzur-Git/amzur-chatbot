from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.workflow import (
    SheetsEmailWorkflowTriggerRequest,
    SheetsEmailWorkflowTriggerResponse,
    WorkflowRunRequest,
    WorkflowRunResponse,
)
from app.services.attachment_service import AttachmentService
from app.services.workflow_orchestrator_service import WorkflowOrchestratorService
from app.core.config import settings


router = APIRouter(prefix="/api/workflows", tags=["workflows"])


@router.post("/research-image-run", response_model=WorkflowRunResponse)
async def run_research_image_workflow(
    payload: WorkflowRunRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WorkflowRunResponse:
    result = await WorkflowOrchestratorService.run(
        db=db,
        user=current_user,
        payload=payload,
    )
    return WorkflowRunResponse(**result)


@router.post("/sheets-email-run", response_model=SheetsEmailWorkflowTriggerResponse)
async def run_sheets_email_workflow(
    payload: SheetsEmailWorkflowTriggerRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SheetsEmailWorkflowTriggerResponse:
    attachment = await AttachmentService.get_by_id(db, current_user, payload.file_id)
    if attachment.file_type != "table":
        raise HTTPException(status_code=400, detail="Selected file is not a CSV/Excel table attachment.")

    backend_base_url = settings.N8N_EXCEL_QUERY_EMAIL_BACKEND_BASE_URL
    if not backend_base_url:
        raise HTTPException(
            status_code=503,
            detail="Sheets email workflow backend base URL is not configured.",
        )

    n8n_payload = {
        "backend_base_url": backend_base_url,
        "file_id": str(payload.file_id),
        "question": payload.question,
        "recipient_email": payload.recipient_email or current_user.email,
        "chat_thread_id": str(payload.chat_thread_id) if payload.chat_thread_id else None,
    }

    if settings.N8N_EXCEL_QUERY_EMAIL_SERVICE_EMAIL:
        n8n_payload["service_email"] = settings.N8N_EXCEL_QUERY_EMAIL_SERVICE_EMAIL
    if settings.N8N_EXCEL_QUERY_EMAIL_SERVICE_PASSWORD:
        n8n_payload["service_password"] = settings.N8N_EXCEL_QUERY_EMAIL_SERVICE_PASSWORD

    workflow_response = await WorkflowOrchestratorService.trigger_sheets_email_workflow(payload=n8n_payload)
    return SheetsEmailWorkflowTriggerResponse(accepted=True, workflow_response=workflow_response)
