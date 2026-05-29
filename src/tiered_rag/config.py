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

    # --- Mock tier servers (Phase 3): separate ports per tier ---
    # tier-1 == mock_llm_base_url above (the router backend)
    mock_tier2_base_url: str = "http://localhost:9102/v1"
    mock_tier3_base_url: str = "http://localhost:9103/v1"

    # --- Simulated token cost (Phase 3): USD per 1K tokens ---
    cost_input_per_1k: float = 0.00015
    cost_output_per_1k: float = 0.00060
    tier2_cost_multiplier: float = 3.0   # tier-1 baseline is 1.0
    tier3_cost_multiplier: float = 10.0

    # --- Tier-2 structured extraction (Phase 4) ---
    item_details_path: str = "xlsx/item_details.xlsx"

    # --- Zero-hallucination guardrails (Phase 5) ---
    verify_answers: bool = True            # run the verifier on grounded answers
    alert_webhook_url: str = ""            # empty -> log-only knowledge-gap alerts

    # --- Tier-3 multi-step reasoning (Phase 6) ---
    tier3_max_steps: int = 5               # bound the chain length (cost/latency guard)

    # --- High-scale engineering (Phase 7) ---
    cache_enabled: bool = True
    redis_url: str = "redis://localhost:6379/0"
    cache_similarity_threshold: float = 0.95   # near-duplicate queries only (high bar -> safe hits)
    cache_ttl_seconds: int = 3600
    cache_max_entries: int = 512               # bound the brute-force scan
    cache_key_prefix: str = "tiered_rag:cache"

    # multiple workers per tier (comma-separated); empty -> the single mock_tier{N}_base_url
    mock_tier1_workers: str = ""
    mock_tier2_workers: str = ""
    mock_tier3_workers: str = ""
    health_check_timeout: float = 2.0

    def tier_workers(self, tier: int) -> list[str]:
        raw = {1: self.mock_tier1_workers, 2: self.mock_tier2_workers,
               3: self.mock_tier3_workers}.get(tier, "")
        urls = [u.strip() for u in raw.split(",") if u.strip()]
        if urls:
            return urls
        single = {1: self.mock_llm_base_url, 2: self.mock_tier2_base_url,
                  3: self.mock_tier3_base_url}.get(tier, self.mock_llm_base_url)
        return [single]


def get_settings() -> Settings:
    return Settings()
