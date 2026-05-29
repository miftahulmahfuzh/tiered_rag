from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    ollama_host: str = "http://localhost:11434"
    embed_model: str = "nomic-embed-text:v1.5"
    embed_dim: int = 768
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "knowledge_base"
    confidence_threshold: float = 0.6


def get_settings() -> Settings:
    return Settings()
