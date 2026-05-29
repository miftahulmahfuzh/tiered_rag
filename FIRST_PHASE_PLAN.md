# Phase 1 — RAG Foundation & Grounded Retrieval — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:executing-plans to
> implement this plan task-by-task. Use superpowers-extended-cc:test-driven-development
> for every task (RED → GREEN → COMMIT).

**Goal:** Build a real semantic-search retrieval layer (ollama `nomic-embed-text:v1.5`
+ Qdrant) that returns matches with a confidence score and **abstains ("I don't know")**
when the top score is below a configurable threshold.

**Architecture:** A thin `Embedder` protocol (ollama implementation + a deterministic
fake for tests) feeds a `QdrantStore` wrapper. `ingest.py` loads `knowledge_base.xlsx`
into a Qdrant collection. `Retriever` embeds the query, searches, and decides
`abstain = top_score < threshold`. All unit tests run **offline** against an in-memory
Qdrant (`location=":memory:"`) with the fake embedder; one `@integration` test exercises
real ollama + a real Qdrant.

**Tech Stack:** Python 3.10+, `qdrant-client`, `httpx` (ollama API), `pandas`+`openpyxl`
(xlsx), `pydantic`/`pydantic-settings` (config), `pytest`.

**Key facts baked into this plan:**
- `nomic-embed-text:v1.5` → **768-dim** vectors; Qdrant distance = **COSINE**.
- nomic requires task prefixes: prepend `search_document: ` to stored docs and
  `search_query: ` to queries. The `OllamaEmbedder` does this internally.
- ollama embed endpoint: `POST {OLLAMA_HOST}/api/embed` with
  `{"model": ..., "input": [texts]}` → `{"embeddings": [[...], ...]}`.
- With COSINE in Qdrant, the returned `score` is cosine similarity in `[-1, 1]`
  (≈`[0,1]` for these embeddings); the **threshold** lives in config (default `0.6`).

---

## Task 0: Project scaffolding & config

**Files:**
- Create: `requirements.txt`, `pyproject.toml` (pytest config), `.env.example`
- Modify: `.gitignore`
- Create: `src/tiered_rag/__init__.py`, `src/tiered_rag/config.py`
- Test: `tests/test_config.py`, `tests/__init__.py`, `tests/conftest.py`

**Step 1: Write the failing test** (`tests/test_config.py`)
```python
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
```

**Step 2: Run → expect FAIL** (`ModuleNotFoundError: tiered_rag.config`)
Run: `pytest tests/test_config.py -v`

**Step 3: Implement**
- `requirements.txt`:
  ```
  qdrant-client>=1.9
  httpx>=0.27
  pandas>=2.0
  openpyxl>=3.1
  pydantic>=2.6
  pydantic-settings>=2.2
  pytest>=8.0
  ```
- `pyproject.toml` (minimal — pytest discovery + src layout + markers):
  ```toml
  [tool.pytest.ini_options]
  pythonpath = ["src"]
  testpaths = ["tests"]
  markers = ["integration: hits real ollama/qdrant (deselect with -m 'not integration')"]
  ```
- `.gitignore`: append `.env`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `qdrant_storage/`
- `.env.example`:
  ```
  OLLAMA_HOST=http://localhost:11434
  EMBED_MODEL=nomic-embed-text:v1.5
  EMBED_DIM=768
  QDRANT_URL=http://localhost:6333
  QDRANT_COLLECTION=knowledge_base
  CONFIDENCE_THRESHOLD=0.6
  # Phase 2+: LLM_TYPE=mock | openai ; OPENAI_API_KEY=... ; OPENAI_MODEL=gpt-5.4-nano
  ```
- `src/tiered_rag/config.py`:
  ```python
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
  ```
- `tests/conftest.py`: empty for now (fixtures added in later tasks).

**Step 4: Run → expect PASS**
Run: `pytest tests/test_config.py -v`

**Step 5: Commit**
```bash
git add requirements.txt pyproject.toml .env.example .gitignore src tests
git commit -m "feat(p1): project scaffolding + config"
```

---

## Task 1: Knowledge base data + loader

**Files:**
- Create: `scripts/build_knowledge_base.py` (generates the xlsx so it's reproducible)
- Create: `xlsx/knowledge_base.xlsx` (generated artifact, committed)
- Create: `src/tiered_rag/knowledge_base.py`
- Test: `tests/test_knowledge_base.py`

**Domain:** a fictional game-store / account support desk (greeting, account, billing,
orders, items). Columns: `id` (int), `question` (str), `answer` (str), `category` (str).
Provide **20 rows** spanning categories: Account, Billing, Orders, Items, General.

**Step 1: Write the failing test**
```python
from tiered_rag.knowledge_base import load_knowledge_base

def test_loads_twenty_qa_pairs():
    rows = load_knowledge_base("xlsx/knowledge_base.xlsx")
    assert len(rows) == 20
    first = rows[0]
    assert {"id", "question", "answer", "category"} <= first.keys()
    assert all(r["question"] and r["answer"] for r in rows)
    assert len({r["id"] for r in rows}) == 20  # unique ids
```

**Step 2: Run → expect FAIL**
Run: `pytest tests/test_knowledge_base.py -v`

**Step 3: Implement**
- `scripts/build_knowledge_base.py`: a dict of 20 Q&A pairs → `pandas.DataFrame` →
  `df.to_excel("xlsx/knowledge_base.xlsx", index=False)`. Run it once to generate the file.
  (Authoring the 20 pairs is part of this task — keep answers self-contained and factual so
  retrieval is unambiguous.)
- `src/tiered_rag/knowledge_base.py`:
  ```python
  import pandas as pd

  def load_knowledge_base(path: str) -> list[dict]:
      df = pd.read_excel(path)
      return df.to_dict(orient="records")
  ```

**Step 4: Run** the generator, then the test → expect PASS
```bash
python scripts/build_knowledge_base.py
pytest tests/test_knowledge_base.py -v
```

**Step 5: Commit**
```bash
git add scripts/build_knowledge_base.py xlsx/knowledge_base.xlsx src/tiered_rag/knowledge_base.py tests/test_knowledge_base.py
git commit -m "feat(p1): knowledge_base.xlsx (20 Q&A) + loader"
```

---

## Task 2: Embedder protocol + ollama client + fake

**Files:**
- Create: `src/tiered_rag/embeddings.py`
- Test: `tests/test_embeddings.py`
- Modify: `tests/conftest.py` (add `fake_embedder` fixture)

**Design:** `Embedder` is a Protocol with `embed_documents(list[str])` and
`embed_query(str)`. `OllamaEmbedder` prepends nomic prefixes and calls `/api/embed`.
`FakeEmbedder` returns deterministic, normalized vectors derived from a hash of the text
(same text → same vector; similar text need not be similar — that's fine for unit tests
because we control which exact strings are stored vs queried).

**Step 1: Write the failing test**
```python
from tiered_rag.embeddings import FakeEmbedder

def test_fake_embedder_is_deterministic_and_right_dim():
    e = FakeEmbedder(dim=768)
    v1 = e.embed_query("hello")
    v2 = e.embed_query("hello")
    assert len(v1) == 768 and v1 == v2
    assert e.embed_query("hello") != e.embed_query("world")

def test_fake_embed_documents_batches():
    e = FakeEmbedder(dim=8)
    vecs = e.embed_documents(["a", "b", "c"])
    assert len(vecs) == 3 and all(len(v) == 8 for v in vecs)
```

**Step 2: Run → expect FAIL**
Run: `pytest tests/test_embeddings.py -v`

**Step 3: Implement** `embeddings.py`
```python
from __future__ import annotations
import hashlib, math
from typing import Protocol
import httpx

class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...

def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]

class FakeEmbedder:
    def __init__(self, dim: int = 768): self.dim = dim
    def _vec(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode()).digest()
        raw = [h[i % len(h)] - 128 for i in range(self.dim)]
        return _normalize([float(x) for x in raw])
    def embed_documents(self, texts): return [self._vec(t) for t in texts]
    def embed_query(self, text): return self._vec(text)

class OllamaEmbedder:
    def __init__(self, host: str, model: str, timeout: float = 60.0):
        self.host, self.model, self.timeout = host.rstrip("/"), model, timeout
    def _embed(self, inputs: list[str]) -> list[list[float]]:
        r = httpx.post(f"{self.host}/api/embed",
                       json={"model": self.model, "input": inputs},
                       timeout=self.timeout)
        r.raise_for_status()
        return r.json()["embeddings"]
    def embed_documents(self, texts):
        return self._embed([f"search_document: {t}" for t in texts])
    def embed_query(self, text):
        return self._embed([f"search_query: {text}"])[0]
```
Add to `tests/conftest.py`:
```python
import pytest
from tiered_rag.embeddings import FakeEmbedder

@pytest.fixture
def fake_embedder():
    return FakeEmbedder(dim=64)  # small dim → fast tests
```

**Step 4: Run → expect PASS**
Run: `pytest tests/test_embeddings.py -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/embeddings.py tests/test_embeddings.py tests/conftest.py
git commit -m "feat(p1): Embedder protocol + OllamaEmbedder + FakeEmbedder"
```

---

## Task 3: Qdrant store wrapper

**Files:**
- Create: `src/tiered_rag/vector_store.py`
- Test: `tests/test_vector_store.py`
- Modify: `tests/conftest.py` (add in-memory `store` fixture)

**Design:** `QdrantStore` wraps `QdrantClient`. Constructor accepts a client so tests pass
`QdrantClient(location=":memory:")`. Methods: `recreate(dim)`, `upsert(points)` where each
point is `{id, vector, payload}`, and `search(vector, limit) -> list[Hit]` with
`Hit = {id, score, payload}`.

**Step 1: Write the failing test**
```python
from qdrant_client import QdrantClient
from tiered_rag.vector_store import QdrantStore

def test_upsert_then_search_returns_nearest(fake_embedder):
    store = QdrantStore(QdrantClient(location=":memory:"), collection="t")
    store.recreate(dim=64)
    docs = ["how to reset password", "order shipping times", "refund policy"]
    store.upsert([
        {"id": i, "vector": v, "payload": {"text": d}}
        for i, (d, v) in enumerate(zip(docs, fake_embedder.embed_documents(docs)))
    ])
    hits = store.search(fake_embedder.embed_query("how to reset password"), limit=3)
    assert hits[0].payload["text"] == "how to reset password"
    assert hits[0].score == max(h.score for h in hits)
```

**Step 2: Run → expect FAIL**
Run: `pytest tests/test_vector_store.py -v`

**Step 3: Implement** `vector_store.py`
```python
from dataclasses import dataclass
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

@dataclass
class Hit:
    id: int | str
    score: float
    payload: dict

class QdrantStore:
    def __init__(self, client: QdrantClient, collection: str):
        self.client, self.collection = client, collection
    def recreate(self, dim: int):
        self.client.recreate_collection(
            self.collection,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
    def upsert(self, points: list[dict]):
        self.client.upsert(self.collection, points=[
            PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"])
            for p in points
        ])
    def search(self, vector: list[float], limit: int = 3) -> list[Hit]:
        res = self.client.search(self.collection, query_vector=vector, limit=limit)
        return [Hit(id=r.id, score=r.score, payload=r.payload) for r in res]
```

**Step 4: Run → expect PASS**
Run: `pytest tests/test_vector_store.py -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/vector_store.py tests/test_vector_store.py tests/conftest.py
git commit -m "feat(p1): Qdrant store wrapper (in-memory testable)"
```

---

## Task 4: Ingest pipeline

**Files:**
- Create: `src/tiered_rag/ingest.py`
- Test: `tests/test_ingest.py`

**Design:** `ingest(rows, store, embedder)` embeds each row's *question* (queries are
matched against questions; the answer rides along in the payload) and upserts. Payload =
`{"question", "answer", "category", "id"}`. Returns count ingested. A CLI `main()` wires
real `Settings` + `OllamaEmbedder` + remote Qdrant for actual deployment.

**Step 1: Write the failing test**
```python
from qdrant_client import QdrantClient
from tiered_rag.vector_store import QdrantStore
from tiered_rag.ingest import ingest

def test_ingest_counts_and_is_searchable(fake_embedder):
    store = QdrantStore(QdrantClient(location=":memory:"), collection="kb")
    rows = [
        {"id": 1, "question": "reset my password", "answer": "Go to Settings > Security.", "category": "Account"},
        {"id": 2, "question": "track my order", "answer": "Use the Orders tab.", "category": "Orders"},
    ]
    n = ingest(rows, store, fake_embedder)
    assert n == 2
    hits = store.search(fake_embedder.embed_query("reset my password"), limit=1)
    assert hits[0].payload["answer"] == "Go to Settings > Security."
```

**Step 2: Run → expect FAIL**
Run: `pytest tests/test_ingest.py -v`

**Step 3: Implement** `ingest.py`
```python
from .embeddings import Embedder
from .vector_store import QdrantStore

def ingest(rows: list[dict], store: QdrantStore, embedder: Embedder) -> int:
    vectors = embedder.embed_documents([r["question"] for r in rows])
    store.recreate(dim=len(vectors[0]))
    store.upsert([
        {"id": r["id"], "vector": v,
         "payload": {"question": r["question"], "answer": r["answer"],
                     "category": r["category"], "id": r["id"]}}
        for r, v in zip(rows, vectors)
    ])
    return len(rows)

def main():  # real deployment path
    from qdrant_client import QdrantClient
    from .config import get_settings
    from .embeddings import OllamaEmbedder
    from .knowledge_base import load_knowledge_base
    s = get_settings()
    store = QdrantStore(QdrantClient(url=s.qdrant_url), s.qdrant_collection)
    emb = OllamaEmbedder(s.ollama_host, s.embed_model)
    n = ingest(load_knowledge_base("xlsx/knowledge_base.xlsx"), store, emb)
    print(f"ingested {n} rows into '{s.qdrant_collection}'")

if __name__ == "__main__":
    main()
```

**Step 4: Run → expect PASS**
Run: `pytest tests/test_ingest.py -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/ingest.py tests/test_ingest.py
git commit -m "feat(p1): ingest pipeline (xlsx → Qdrant)"
```

---

## Task 5: Retriever with confidence threshold + "I don't know"

**Files:**
- Create: `src/tiered_rag/retrieval.py`
- Test: `tests/test_retrieval.py`

**Design:** `RetrievalResult` dataclass: `abstain: bool`, `score: float`, `hits: list[Hit]`,
`answer: str | None`. `Retriever(store, embedder, threshold)` embeds the query, searches,
and if `top.score < threshold` → `abstain=True, answer=None`; else returns the top answer.
The "I don't know" message string is owned by the caller/API, not hardcoded here — the
retriever just reports `abstain`.

**Step 1: Write the failing test**
```python
from qdrant_client import QdrantClient
from tiered_rag.vector_store import QdrantStore
from tiered_rag.ingest import ingest
from tiered_rag.retrieval import Retriever

def _retriever(fake_embedder, threshold):
    store = QdrantStore(QdrantClient(location=":memory:"), collection="kb")
    ingest([
        {"id": 1, "question": "how do I reset my password",
         "answer": "Open Settings > Security > Reset.", "category": "Account"},
    ], store, fake_embedder)
    return Retriever(store, fake_embedder, threshold=threshold)

def test_confident_hit_returns_answer(fake_embedder):
    # exact-match query → cosine 1.0 with FakeEmbedder → above any threshold < 1
    r = _retriever(fake_embedder, threshold=0.6).retrieve("how do I reset my password")
    assert r.abstain is False
    assert r.answer == "Open Settings > Security > Reset."
    assert r.score >= 0.99

def test_low_confidence_triggers_i_dont_know(fake_embedder):
    # unrelated query + impossibly high threshold → must abstain
    r = _retriever(fake_embedder, threshold=0.999).retrieve("what is the capital of France")
    assert r.abstain is True
    assert r.answer is None
```

**Step 2: Run → expect FAIL**
Run: `pytest tests/test_retrieval.py -v`

**Step 3: Implement** `retrieval.py`
```python
from dataclasses import dataclass
from .embeddings import Embedder
from .vector_store import QdrantStore, Hit

@dataclass
class RetrievalResult:
    abstain: bool
    score: float
    hits: list[Hit]
    answer: str | None

class Retriever:
    def __init__(self, store: QdrantStore, embedder: Embedder, threshold: float):
        self.store, self.embedder, self.threshold = store, embedder, threshold
    def retrieve(self, query: str, limit: int = 3) -> RetrievalResult:
        hits = self.store.search(self.embedder.embed_query(query), limit=limit)
        if not hits or hits[0].score < self.threshold:
            top = hits[0].score if hits else 0.0
            return RetrievalResult(abstain=True, score=top, hits=hits, answer=None)
        return RetrievalResult(abstain=False, score=hits[0].score,
                               hits=hits, answer=hits[0].payload["answer"])
```

**Step 4: Run → expect PASS**
Run: `pytest tests/test_retrieval.py -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/retrieval.py tests/test_retrieval.py
git commit -m "feat(p1): Retriever with confidence threshold + abstain state"
```

---

## Task 6: Abstention evaluation harness

**Files:**
- Create: `src/tiered_rag/eval_abstention.py`
- Create: `tests/data/eval_questions.py` (in-scope + out-of-scope labeled questions)
- Test: `tests/test_eval_abstention.py`

**Design:** an eval set of labeled questions: `should_answer=True` (paraphrases of KB
questions) and `should_answer=False` (clearly out-of-domain). `evaluate(retriever, dataset)`
returns metrics: `abstention_rate` on out-of-scope, `false_abstention_rate` on in-scope,
and per-item records. This is the seed of the EVAL_REPORT "Abstention Rate" number.

**Step 1: Write the failing test**
```python
from qdrant_client import QdrantClient
from tiered_rag.vector_store import QdrantStore
from tiered_rag.ingest import ingest
from tiered_rag.retrieval import Retriever
from tiered_rag.eval_abstention import evaluate

def test_metrics_shape(fake_embedder):
    store = QdrantStore(QdrantClient(location=":memory:"), collection="kb")
    ingest([{"id": 1, "question": "reset password",
             "answer": "Settings > Security.", "category": "Account"}],
           store, fake_embedder)
    r = Retriever(store, fake_embedder, threshold=0.6)
    dataset = [
        {"q": "reset password", "should_answer": True},     # exact → answered
        {"q": "weather on mars", "should_answer": False},    # OOD → abstain
    ]
    m = evaluate(r, dataset)
    assert 0.0 <= m["abstention_rate"] <= 1.0
    assert set(m) >= {"abstention_rate", "false_abstention_rate", "records"}
    assert len(m["records"]) == 2
```

**Step 2: Run → expect FAIL**
Run: `pytest tests/test_eval_abstention.py -v`

**Step 3: Implement** `eval_abstention.py`
```python
from .retrieval import Retriever

def evaluate(retriever: Retriever, dataset: list[dict]) -> dict:
    records, ood_total, ood_abstained, ans_total, ans_abstained = [], 0, 0, 0, 0
    for item in dataset:
        res = retriever.retrieve(item["q"])
        records.append({"q": item["q"], "should_answer": item["should_answer"],
                        "abstained": res.abstain, "score": res.score})
        if item["should_answer"]:
            ans_total += 1; ans_abstained += int(res.abstain)
        else:
            ood_total += 1; ood_abstained += int(res.abstain)
    return {
        "abstention_rate": (ood_abstained / ood_total) if ood_total else 0.0,
        "false_abstention_rate": (ans_abstained / ans_total) if ans_total else 0.0,
        "records": records,
    }
```
`tests/data/eval_questions.py`: lists of in-scope paraphrases (matching the 20 KB rows)
and out-of-scope questions, for use by the real-ollama integration test in Task 7.

**Step 4: Run → expect PASS**
Run: `pytest tests/test_eval_abstention.py -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/eval_abstention.py tests/data tests/test_eval_abstention.py
git commit -m "feat(p1): abstention evaluation harness"
```

---

## Task 7: Docker (Qdrant) + real-ollama integration test + Phase-1 README note

**Files:**
- Create: `docker-compose.yml` (Qdrant service)
- Create: `tests/test_integration_rag.py` (marked `@pytest.mark.integration`)
- Create: `README.md` (Phase 1 section only; grows later)

**Step 1: Write the failing/skippable integration test**
```python
import os, pytest, httpx
from qdrant_client import QdrantClient
from tiered_rag.config import get_settings
from tiered_rag.embeddings import OllamaEmbedder
from tiered_rag.vector_store import QdrantStore
from tiered_rag.knowledge_base import load_knowledge_base
from tiered_rag.ingest import ingest
from tiered_rag.retrieval import Retriever

pytestmark = pytest.mark.integration

def _ollama_up(host):
    try: return httpx.get(f"{host}/api/tags", timeout=2).status_code == 200
    except Exception: return False

def test_real_rag_end_to_end():
    s = get_settings()
    if not _ollama_up(s.ollama_host):
        pytest.skip("ollama not running")
    emb = OllamaEmbedder(s.ollama_host, s.embed_model)
    store = QdrantStore(QdrantClient(url=s.qdrant_url), s.qdrant_collection)
    ingest(load_knowledge_base("xlsx/knowledge_base.xlsx"), store, emb)
    r = Retriever(store, emb, s.confidence_threshold)
    in_scope = r.retrieve("how can I change my password?")
    out_scope = r.retrieve("who won the 1998 world cup?")
    assert in_scope.abstain is False
    assert out_scope.abstain is True
```

**Step 2: Run → expect SKIP** (until ollama + Qdrant are up)
Run: `pytest -m integration -v`

**Step 3: Implement infra + bring services up**
- `docker-compose.yml`:
  ```yaml
  services:
    qdrant:
      image: qdrant/qdrant:latest
      ports: ["6333:6333", "6334:6334"]
      volumes: ["./qdrant_storage:/qdrant/storage"]
  ```
- Bring up: `docker compose up -d qdrant`
- Ensure ollama model present: `ollama pull nomic-embed-text:v1.5`
- `README.md`: a Phase-1 "RAG Foundation" section — setup (`pip install -r requirements.txt`,
  `docker compose up -d qdrant`, `ollama pull nomic-embed-text:v1.5`,
  `python scripts/build_knowledge_base.py`, `python -m tiered_rag.ingest`), how the
  abstention threshold works, and how to run tests
  (`pytest -m 'not integration'` for the offline suite, `pytest -m integration` for the
  real run).

**Step 4: Run the full suite**
```bash
pytest -m "not integration" -v      # all offline unit tests PASS
pytest -m integration -v            # PASS when ollama+qdrant up, else SKIP
```

**Step 5: Commit**
```bash
git add docker-compose.yml tests/test_integration_rag.py README.md
git commit -m "feat(p1): qdrant compose + ollama integration test + README"
```

---

## Phase 1 Definition of Done

- [ ] `pytest -m "not integration"` → all green, fully offline.
- [ ] `docker compose up -d qdrant` + `ollama pull nomic-embed-text:v1.5` +
      `python -m tiered_rag.ingest` populates the collection.
- [ ] `pytest -m integration` → real ollama+Qdrant: in-scope answered, out-of-scope abstains.
- [ ] `retrieve()` returns a confidence score and an honest **"I don't know"** (abstain)
      below threshold.
- [ ] README Phase-1 section written. All work committed.

**Next:** write `SECOND_PHASE_PLAN.md` (Tier Routing Engine) once Phase 1 is green.
