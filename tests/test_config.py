from tiered_rag.config import Settings


def test_defaults():
    s = Settings()
    assert s.embed_model == "nomic-embed-text:v1.5"
    assert s.embed_dim == 768
    assert s.qdrant_collection == "knowledge_base"
    assert s.confidence_threshold == 0.6
    assert s.ollama_host.startswith("http")


def test_env_override(monkeypatch):
    monkeypatch.setenv("CONFIDENCE_THRESHOLD", "0.8")
    assert Settings().confidence_threshold == 0.8


def test_llm_defaults():
    s = Settings()
    assert s.llm_type == "openai"
    assert s.openai_model  # non-empty default
    assert s.openai_base_url.startswith("http")
    assert s.mock_llm_base_url.startswith("http")
    assert s.router_temperature == 0.0


def test_llm_type_override(monkeypatch):
    monkeypatch.setenv("LLM_TYPE", "mock")
    assert Settings().llm_type == "mock"


def test_phase3_mock_and_cost_defaults():
    s = Settings()
    # tier-1 mock is the existing mock_llm_base_url (:9101)
    assert s.mock_llm_base_url.endswith(":9101/v1")
    assert s.mock_tier2_base_url.endswith(":9102/v1")
    assert s.mock_tier3_base_url.endswith(":9103/v1")
    assert s.cost_input_per_1k > 0 and s.cost_output_per_1k > 0
    # deeper tiers are simulated as more expensive
    assert s.tier3_cost_multiplier > s.tier2_cost_multiplier > 1.0


def test_phase4_item_details_path_default():
    assert Settings().item_details_path.endswith("item_details.xlsx")


def test_phase5_guardrail_defaults():
    s = Settings()
    assert s.alert_webhook_url == ""        # log-only by default
    assert s.verify_answers is True         # guardrail on by default


def test_phase6_tier3_max_steps_default():
    s = Settings()
    assert s.tier3_max_steps == 5
    assert s.tier3_max_steps > 0


def test_phase7_cache_defaults():
    s = Settings()
    assert s.cache_enabled is True
    assert 0.0 < s.cache_similarity_threshold <= 1.0
    assert s.cache_ttl_seconds > 0 and s.cache_max_entries > 0
    assert s.redis_url.startswith("redis://")
    assert s.cache_key_prefix  # non-empty


def test_phase7_worker_and_health_defaults():
    s = Settings()
    # empty by default -> build_llm falls back to the single per-tier base url (Phase-3 behaviour)
    assert s.mock_tier1_workers == "" and s.mock_tier2_workers == "" and s.mock_tier3_workers == ""
    assert s.health_check_timeout > 0


def test_phase7_worker_list_parses(monkeypatch):
    monkeypatch.setenv("MOCK_TIER1_WORKERS", "http://a:9101/v1, http://b:9111/v1")
    assert Settings().tier_workers(1) == ["http://a:9101/v1", "http://b:9111/v1"]


def test_phase7_worker_list_falls_back_to_single_url():
    s = Settings()
    assert s.tier_workers(1) == [s.mock_llm_base_url]      # no workers configured -> single tier-1 url
    assert s.tier_workers(2) == [s.mock_tier2_base_url]


def test_phase8_telegram_defaults():
    s = Settings()
    assert s.telegram_bot_token == ""          # real value lives only in .env (gitignored)
    assert s.telegram_webhook_secret == ""
    assert s.telegram_api_base.startswith("https://api.telegram.org")


def test_phase8_telegram_token_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    assert Settings().telegram_bot_token == "123:ABC"
