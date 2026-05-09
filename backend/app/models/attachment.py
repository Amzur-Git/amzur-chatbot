from datetime import datetime
import uuid

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.models.user import Base


class Attachment(Base):
    __tablename__ = "attachments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id = Column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    thread_id = Column(
        UUID(as_uuid=True),
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    file_type = Column(String(32), nullable=False, index=True)
    file_name = Column(String(255), nullable=False)
    file_path = Column(Text, nullable=False, unique=True)
    file_size = Column(BigInteger, nullable=False)
    metadata_json = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    message = relationship("Message", back_populates="attachments")
    thread = relationship("Thread", back_populates="attachments")
    user = relationship("User", back_populates="attachments")
