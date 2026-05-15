from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.research_digest import ResearchDigestStreamRequest
from app.services.research_digest_service import ResearchDigestService


router = APIRouter(prefix="/api/research-digest", tags=["research-digest"])


@router.post("/stream")
async def stream_research_digest(
    request: ResearchDigestStreamRequest,
    current_user: User = Depends(get_current_user),
):
    del current_user  # Route is authenticated; user identity is not needed in v1.

    stream = ResearchDigestService.stream_digest(
        topic=request.topic,
        max_rounds=request.max_rounds,
        papers_per_round=request.papers_per_round,
        min_papers=request.min_papers,
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
