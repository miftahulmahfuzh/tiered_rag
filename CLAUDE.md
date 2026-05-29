# CLAUDE.md

Guidance for working in this repo. Keep it concise; update as the project grows.

## What this is

`tiered_rag` — a zero-hallucination, multi-tier support chatbot backend (Garena take-home).
Built **incrementally in 8 phases**. Read `MAJOR_PHASES.md` for the roadmap and the current
`*_PHASE_PLAN.md` for the active phase's detailed TDD steps. Track work via native tasks
(`TaskList`).

## Architecture (locked decisions)

- **LLM backend is feature-flagged** via `LLM_TYPE`:
  - `mock` → mock local endpoints on separate ports (brief-compliant, offline, deterministic).
  - `openai` → one real OpenAI model (`OPENAI_MODEL`) behind **all three tiers**.
- **RAG is always real**: ollama `nomic-embed-text:v1.5` (768-dim) + Qdrant (COSINE).
  A confidence threshold drives the **"I don't know" abstain** state.
- **Router**: the cheap **Tier-1 LLM is always the entry point and decides the tier** (1/2/3).
  Tier 1 carries its plan inline; Tier 2/3 then call their own LLM to build a pipeline /
  multi-step plan. Execute plan → assemble `final_input_context` → final synthesis.

## Layout

- `src/tiered_rag/` — package (config, embeddings, vector_store, ingest, retrieval, …).
- `tests/` — pytest. `xlsx/` — data. `scripts/` — one-off generators.
- `docker-compose.yml` — Qdrant now; grows to +mock LLMs (P3), +Redis (P7).

## Conventions

- **TDD**: RED → GREEN → commit, one bite-sized task at a time (see the phase plan).
- **Tests run offline by default**: in-memory Qdrant (`QdrantClient(location=":memory:")`) +
  `FakeEmbedder`. Tests needing real services are marked `@pytest.mark.integration` and skip
  if the service is down.
- **Config** comes only from `Settings` (pydantic-settings, reads `.env`). Never hardcode
  hosts/keys/thresholds.
- **Secrets** live only in gitignored `.env`; `.env.example` holds placeholders.

## Commands

```bash
pip install -r requirements.txt
docker compose up -d qdrant
ollama pull nomic-embed-text:v1.5
python scripts/build_knowledge_base.py     # regenerate xlsx/knowledge_base.xlsx
python -m tiered_rag.ingest                 # load KB into Qdrant
pytest -m "not integration"                 # fast offline suite
pytest -m integration                       # real ollama+Qdrant (skips if down)
```

## Environment notes

Python 3.12 (miniconda). No GPU; nomic-embed runs fine on CPU. Ollama must be running for
ingest and integration tests.

> [!IMPORTANT]

> when user type in "p" : you must git add . commit push in a new subagent. USE haiku model for this task.
