import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.attachments import router as attachments_router
from app.api.generate_image import router as generate_image_router
from app.core.config import settings

logger = logging.getLogger("uvicorn.error")

app = FastAPI(title=settings.APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(attachments_router)
app.include_router(generate_image_router)


@app.on_event("startup")
async def log_startup_configuration() -> None:
    logger.info(
        "Image generation configured via LiteLLM: model=%s timeout=%ss rate_limit=%s/hr",
        settings.IMAGE_GEN_MODEL,
        settings.IMAGE_GEN_TIMEOUT_SECONDS,
        settings.IMAGE_GEN_RATE_LIMIT,
    )

@app.get("/")
async def root():
    return {"message": f"{settings.APP_NAME} is running!"}