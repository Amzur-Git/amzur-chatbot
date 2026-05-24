import logging
import urllib.parse

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.attachments import router as attachments_router
from app.api.generate_image import router as generate_image_router
from app.api.research_digest import router as research_digest_router
from app.api.sheets import router as sheets_router
from app.api.workflows import router as workflows_router
from app.core.config import settings

logger = logging.getLogger("uvicorn.error")

app = FastAPI(title=settings.APP_NAME)


def _normalize_dev_hostname(hostname: str) -> str:
    return "localhost" if hostname == "0.0.0.0" else hostname


def _build_cors_origins() -> list[str]:
    origins: list[str] = []

    configured = [
        value.strip()
        for value in settings.FRONTEND_URLS.split(",")
        if value.strip()
    ]

    if settings.FRONTEND_URL:
        configured.insert(0, settings.FRONTEND_URL)

    for url in configured:
        try:
            parsed = urllib.parse.urlparse(url)
            if not parsed.scheme or not parsed.hostname:
                continue

            hostname = _normalize_dev_hostname(parsed.hostname)
            default_port = 443 if parsed.scheme == "https" else 80
            port_suffix = f":{parsed.port}" if parsed.port and parsed.port != default_port else ""
            origin = f"{parsed.scheme}://{hostname}{port_suffix}"
            if origin not in origins:
                origins.append(origin)

            # For local development, accept common loopback aliases.
            if hostname in {"localhost", "127.0.0.1"}:
                for alternate in ("localhost", "127.0.0.1", "0.0.0.0"):
                    alternate_origin = f"{parsed.scheme}://{alternate}{port_suffix}"
                    if alternate_origin not in origins:
                        origins.append(alternate_origin)
        except Exception:
            continue

    return origins or ["http://localhost:5173", "http://127.0.0.1:5173"]


def _is_production() -> bool:
    return settings.ENVIRONMENT.lower() == "production"

app.add_middleware(
    CORSMiddleware,
    allow_origins=_build_cors_origins(),
    allow_origin_regex=None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(attachments_router)
app.include_router(generate_image_router)
app.include_router(sheets_router)
app.include_router(research_digest_router)
app.include_router(workflows_router)


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