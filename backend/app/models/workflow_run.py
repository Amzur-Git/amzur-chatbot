from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.models.user import Base


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    thread_id = Column(UUID(as_uuid=True), ForeignKey("threads.id"), nullable=False, index=True)

    user_request = Column(Text, nullable=False)
    topic = Column(String(300), nullable=False)
    status = Column(String(32), nullable=False, default="running")

    step_results = Column(JSON, nullable=False, default=dict)
    error_messages = Column(JSON, nullable=False, default=list)

    digest_text = Column(Text, nullable=True)
    digest_message_id = Column(UUID(as_uuid=True), nullable=True)

    image_prompt = Column(Text, nullable=True)
    image_message_id = Column(UUID(as_uuid=True), nullable=True)
    image_attachment_id = Column(UUID(as_uuid=True), nullable=True)

    slack_delivery_status = Column(String(32), nullable=True)

    started_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
