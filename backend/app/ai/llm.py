from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from openai import OpenAI
from app.core.config import settings

# LangChain LLM (for chains)
llm = ChatOpenAI(
    model=settings.LLM_MODEL,
    base_url=settings.LITELLM_PROXY_URL,
    api_key=settings.LITELLM_API_KEY,
    timeout=30,
    max_retries=2,
)

# OpenAI SDK client (for direct calls)
client = OpenAI(
    api_key=settings.LITELLM_API_KEY,
    base_url=settings.LITELLM_PROXY_URL,
)

# Embeddings
embeddings = OpenAIEmbeddings(
    model=settings.LITELLM_EMBEDDING_MODEL,
    base_url=settings.LITELLM_PROXY_URL,
    api_key=settings.LITELLM_API_KEY,
)