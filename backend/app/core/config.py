from pydantic_settings import BaseSettings
from typing import Optional
from pathlib import Path


ENV_FILE_PATH = Path(__file__).resolve().parents[2] / ".env"

class Settings(BaseSettings):
    # App
    SECRET_KEY: str
    JWT_EXPIRE_MINUTES: int = 480
    APP_NAME: str = "amzur-ai-chat"
    ENVIRONMENT: str = "development"
    
    # Database
    DATABASE_URL: str
    DB_QA_ENABLED: bool = True
    DB_QA_COMMAND_PREFIX: str = "/db"
    DB_QA_ALLOWED_SCHEMAS: str = "public"
    DB_QA_ALLOWED_TABLES: str = ""
    DB_QA_BLOCKED_TABLES: str = "users,messages,threads,attachments"
    DB_QA_MAX_ROWS: int = 50
    DB_QA_MAX_COLUMNS_PER_TABLE: int = 40
    
    # LiteLLM Proxy
    LITELLM_PROXY_URL: str
    LITELLM_API_KEY: str
    LLM_MODEL: str = "gemini/gemini-2.5-flash"
    LITELLM_EMBEDDING_MODEL: str = "text-embedding-3-large"
    IMAGE_GEN_MODEL: str = "gemini/imagen-4.0-fast-generate-001"
    GEMINI_API_KEY: Optional[str] = None
    GOOGLE_API_KEY: Optional[str] = None
    GEMINI_IMAGE_MODEL: str = "gemini-2.5-flash-image"
    IMAGE_GEN_TIMEOUT_SECONDS: int = 90
    IMAGE_GEN_MAX_PER_REQUEST: int = 4
    IMAGE_GEN_DEFAULT_ASPECT_RATIO: str = "1:1"
    IMAGE_GEN_RATE_LIMIT: int = 20
    IMAGE_RETENTION_DAYS: int = 30
    IMAGE_GEN_MAX_PROMPT_CHARS: int = 1000
    IMAGE_GEN_MAX_CONCURRENT_REQUESTS: int = 2

    # Workflow integrations (optional)
    WORKFLOW_SLACK_WEBHOOK_URL: Optional[str] = None
    N8N_EXCEL_QUERY_EMAIL_WEBHOOK_URL: Optional[str] = None
    N8N_EXCEL_QUERY_EMAIL_BACKEND_BASE_URL: Optional[str] = None
    N8N_EXCEL_QUERY_EMAIL_TIMEOUT_SECONDS: int = 20
    N8N_EXCEL_QUERY_EMAIL_SERVICE_EMAIL: Optional[str] = None
    N8N_EXCEL_QUERY_EMAIL_SERVICE_PASSWORD: Optional[str] = None
    
    # LiteLLM User Tracking (ADD THESE THREE LINES)
    LITELLM_USER_ID: str
    LITELLM_DEPARTMENT: str
    LITELLM_ENVIRONMENT: str
    
    # Google OAuth (optional)
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GOOGLE_REDIRECT_URI: Optional[str] = None
    GOOGLE_OAUTH_SCOPES: str = (
        "openid email profile "
        "https://www.googleapis.com/auth/spreadsheets.readonly "
        "https://www.googleapis.com/auth/drive.readonly"
    )
    TOKEN_ENCRYPTION_KEY: Optional[str] = None
    GOOGLE_SERVICE_ACCOUNT_JSON: Optional[str] = None
    GOOGLE_SERVICE_ACCOUNT_FILE: Optional[str] = None
    FRONTEND_URL: str = "http://localhost:5173"
    FRONTEND_URLS: str = "http://localhost:5173"
    
    # ChromaDB
    CHROMA_PERSIST_DIR: str = "./chroma_db"
    
    # File uploads
    MAX_UPLOAD_MB: int = 20
    UPLOAD_DIR: str = "./uploads"
    MAX_IMAGE_UPLOAD_MB: int = 15
    MAX_VIDEO_UPLOAD_MB: int = 80
    MAX_TABLE_UPLOAD_MB: int = 20
    MAX_CODE_UPLOAD_MB: int = 10
    MAX_FORMULA_UPLOAD_MB: int = 2
    MAX_PDF_UPLOAD_MB: int = 30
    SHEETS_UPLOAD_DIR: str = "/mnt/user-data/uploads"
    MAX_ATTACHMENT_CONTEXT_CHARS: int = 12000
    PDF_RAG_CHUNK_SIZE: int = 1400
    PDF_RAG_CHUNK_OVERLAP: int = 200
    PDF_RAG_TOP_K: int = 4
    PDF_RAG_MAX_CONTEXT_CHARS: int = 9000
    
    class Config:
        env_file = str(ENV_FILE_PATH)

settings = Settings()