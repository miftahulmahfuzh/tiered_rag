from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    ollama_host: str = "http://localhost:11434"
    embed_model: str = "nomic-embed-text:v1.5"
    embed_dim: int = 768
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "knowledge_base"
    confidence_threshold: float = 0.6

    # --- LLM backend (Phase 2+) ---
    llm_type: str = "openai"  # "openai" | "mock"
    openai_api_key: str = ""
    openai_model: str = "gpt-5.4-nano"
    openai_base_url: str = "https://api.openai.com/v1"
    mock_llm_base_url: str = "http://localhost:9101/v1"  # Tier-1 mock; servers wired in Phase 3
    router_temperature: float = 0.0


def get_settings() -> Settings:
    return Settings()
