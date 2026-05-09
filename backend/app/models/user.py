from sqlalchemy import Column, String, DateTime, Boolean, Index, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=True)  # Null for Google OAuth users
    google_id = Column(String, nullable=True, unique=True)  # For Google OAuth
    full_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    attachments = relationship("Attachment", back_populates="user")

    __table_args__ = (
        # Enforce case-insensitive uniqueness for email addresses.
        Index("uq_users_email_lower", func.lower(email), unique=True),
    )