from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # App
    SECRET_KEY: str
    JWT_EXPIRE_MINUTES: int = 480
    APP_NAME: str = "amzur-ai-chat"
    ENVIRONMENT: str = "development"
    
    # Database
    DATABASE_URL: str
    
    # LiteLLM Proxy
    LITELLM_PROXY_URL: str
    LITELLM_API_KEY: str
    LLM_MODEL: str = "gemini/gemini-2.5-flash"
    LITELLM_EMBEDDING_MODEL: str = "text-embedding-3-large"
    IMAGE_GEN_MODEL: str = "gemini/imagen-4.0-fast-generate-001"
    
    # LiteLLM User Tracking (ADD THESE THREE LINES)
    LITELLM_USER_ID: str
    LITELLM_DEPARTMENT: str
    LITELLM_ENVIRONMENT: str
    
    # Google OAuth (optional)
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GOOGLE_REDIRECT_URI: Optional[str] = None
    FRONTEND_URL: str = "http://localhost:5173"
    
    # ChromaDB
    CHROMA_PERSIST_DIR: str = "./chroma_db"
    
    # File uploads
    MAX_UPLOAD_MB: int = 20
    UPLOAD_DIR: str = "./uploads"
    
    class Config:
        env_file = ".env"

settings = Settings()