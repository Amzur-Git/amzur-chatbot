from __future__ import annotations

from pydantic import BaseModel, Field


class ResearchDigestStreamRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    max_rounds: int = Field(default=3, ge=1, le=5)
    papers_per_round: int = Field(default=5, ge=3, le=10)
    min_papers: int = Field(default=6, ge=3, le=20)
