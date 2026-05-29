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
