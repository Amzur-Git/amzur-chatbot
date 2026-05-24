from __future__ import annotations

from datetime import datetime
import uuid
from typing import Any

from pydantic import BaseModel, Field


class WorkflowRunRequest(BaseModel):
    chat_thread_id: uuid.UUID
    user_request: str = Field(min_length=3, max_length=4000)
    topic: str | None = Field(default=None, min_length=3, max_length=300)

    max_rounds: int = Field(default=3, ge=1, le=5)
    papers_per_round: int = Field(default=5, ge=3, le=10)
    min_papers: int = Field(default=6, ge=3, le=20)

    num_images: int = Field(default=1, ge=1, le=4)
    aspect_ratio: str | None = None
    image_prompt: str | None = None

    send_slack_dm: bool = False
    slack_webhook_url: str | None = None


class WorkflowStepResult(BaseModel):
    status: str
    message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunResponse(BaseModel):
    run_id: uuid.UUID
    status: str
    topic: str

    digest_message_id: uuid.UUID | None = None
    image_message_id: uuid.UUID | None = None
    image_attachment_id: uuid.UUID | None = None

    step_results: dict[str, WorkflowStepResult] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)

    completed_at: datetime


class SheetsEmailWorkflowTriggerRequest(BaseModel):
    chat_thread_id: uuid.UUID | None = None
    file_id: uuid.UUID
    question: str = Field(min_length=1, max_length=2000)
    recipient_email: str | None = None


class SheetsEmailWorkflowTriggerResponse(BaseModel):
    accepted: bool
    workflow_response: dict[str, Any] = Field(default_factory=dict)
