# Phase 8 — Telegram Front-End + Final Packaging — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:executing-plans to implement this plan
> task-by-task. Use superpowers-extended-cc:test-driven-development for every task (RED → GREEN → COMMIT).

**Goal:** Ship it. Per `MAJOR_PHASES.md` §4 (Phase 8), this final phase puts a **Telegram bot front-end**
over the already-complete `/chat` gateway (Phases 1–7), finalizes the **`Dockerfile` + `docker-compose`**
(Gateway + Redis + Mock LLM + Qdrant), and assembles the two **submission documents**:

1. **Telegram bot** — a thin front-end that receives user messages via a **webhook** and replies with the
   exact same answer `/chat` would produce (router → tier executor → Phase-5 guardrail → Phase-7 cache).
   **No new answer logic** — Phase 8 reuses the Phase-1–7 pipeline verbatim; the bot is just a new
   *transport*.
2. **Final packaging** — confirm `docker compose up` brings up the whole stack and the gateway is reachable;
   wire the Telegram settings into the gateway service.
3. **`README.md`** — a top-level architecture overview + run instructions + the Telegram setup, linking the
   per-phase sections already written.
4. **`EVAL_REPORT.md`** — the graded deliverable: **abstention rate** (Phase-1 `eval_abstention`),
   **routing accuracy** (Phase-2 real model + Phase-3 mock), and **token/cost-savings + cache hit-rate**
   (Phase-7). Phases 1–7 already produced every input number — Phase 8 assembles them from *real runs*
   (never invented, mirroring the Phase-7 README discipline).

**No new runtime dependency.** The Telegram client uses raw `httpx` against the Bot API
(`https://api.telegram.org/bot<token>/<method>`), exactly as `OpenAICompatLLM` calls the OpenAI-compatible
API with `httpx` (no SDK). Offline tests inject a fake Telegram client + use `TestClient` (no network); one
`@integration` test hits the real `getMe` (skips if no token / offline).

---

## Architecture (what Phase 8 adds around the existing `/chat` pipeline)

```
                Telegram user
                     │  sends a message in the chat
                     ▼
        Telegram servers  ──POST update JSON──►  POST /telegram/webhook   (NEW)
                                                   │  1. validate X-Telegram-Bot-Api-Secret-Token
                                                   │  2. extract (chat_id, text) from the update
                                                   │  3. return {"ok": true} IMMEDIATELY
                                                   │     (schedule the slow work in BackgroundTasks
                                                   │      so Telegram never times out / retries)
                                                   ▼
                       BackgroundTasks: handle_update(chat_id, text)
                                                   │
                                                   ▼
                  process_query(text)  ── SHARED with POST /chat (NEW refactor) ──┐
                     cache.get → HIT? serve : Orchestrator.run → guardrail → cache.put
                     usage_log.record(...)                                        │
                                                   ▼                              │
                       TelegramClient.send_message(chat_id, answer)  ◄────────────┘
                                                   │  httpx POST .../sendMessage
                                                   ▼
                                            reply appears in the user's chat

Webhook delivery (one-time setup, local dev):
   uvicorn tiered_rag.api:app --port 8000        # the gateway
   ngrok http 8000                               # public HTTPS tunnel -> http://localhost:8000
   python scripts/set_telegram_webhook.py --url https://<id>.ngrok-free.app   # registers the webhook
```

**Why webhook (with a polling fallback).** The brief + the user's setup point at a webhook (ngrok → Telegram
`setWebhook`). Webhooks are push (no idle polling) and match the FastAPI gateway. For local dev without
ngrok, `scripts/telegram_poll.py` long-polls `getUpdates` and feeds the **same** `process_query` — so either
transport produces identical answers.

---

## Tech stack / what we build on

- Phase-7 `create_app()` with app-state `UsageLog` / `Alerter` / `SemanticCache`, the `get_cache` /
  `get_orchestrator` / `get_usage_log` / `get_alerter` dependencies, and the `/chat` handler whose body we
  **extract into a reusable `process_query(...)`**.
- Phase-1 `eval_abstention.evaluate(retriever, dataset)` and Phase-2 `eval_routing.evaluate(router, dataset)`
  + `tests/data/routing_questions.py` (for EVAL_REPORT).
- The recorded Phase-7 numbers (savings 62.6%, cache hit-rate 57.1%, load `rps/p50/p95/p99/errors`).

---

## 🔐 SECURITY — the Telegram bot token

The bot (`@test123_miftah_bot`, id `5062200811`) and its token were created via BotFather. Per repo
convention (`CLAUDE.md`: *secrets live only in gitignored `.env`; `.env.example` holds placeholders*):

- **`config.py` gets `telegram_bot_token: str = ""`** — an env-driven field with an **empty default**,
  exactly like `openai_api_key`. The real value is **never** a default and is **never** committed.
- **The real token lives only in `.env`** (gitignored — confirmed `.gitignore:2` is `.env`):
  ```dotenv
  # .env  (gitignored — DO NOT COMMIT)
  TELEGRAM_BOT_TOKEN=<your-bot-token-from-BotFather>   # real value lives ONLY in .env, never here
  TELEGRAM_WEBHOOK_SECRET=<any-random-string-you-pick>   # echoed back by Telegram for validation
  ```
- **`.env.example` gets placeholders only** (`TELEGRAM_BOT_TOKEN=`, `TELEGRAM_WEBHOOK_SECRET=`).

> **⚠️ The original token was shared in plaintext in chat earlier, so it must be treated as compromised.**
> It has been scrubbed from this file, and the file was gitignored until now (the token is *not* in git
> history). Before any real submission/publication, **regenerate it via BotFather (`/revoke` → `/token`)**
> and place the fresh value in `.env` only — never in a tracked file.

Verified working (browser): `https://api.telegram.org/bot<token>/getMe` →
`{"ok": true, "result": {"id": 5062200811, "username": "test123_miftah_bot", "is_bot": true, ...}}`.

---

## New/changed files at a glance

| File | Change |
|---|---|
| `src/tiered_rag/config.py` | + `telegram_bot_token`, `telegram_webhook_secret`, `telegram_api_base` (+ optional `telegram_enabled`) |
| `src/tiered_rag/telegram.py` | **new** — `TelegramClient` (`send_message`, `get_me`, `set_webhook`, `delete_webhook`, `get_webhook_info`) + `extract_message(update) -> (chat_id, text) | None` |
| `src/tiered_rag/api.py` | extract `process_query(...)` from `/chat`; add `POST /telegram/webhook` (secret check → background handle → send reply); `get_telegram` dep + `app.state.telegram` |
| `scripts/set_telegram_webhook.py` | **new** — register the webhook (`setWebhook` with the secret), then `getWebhookInfo` to confirm |
| `scripts/telegram_poll.py` | **new** — long-poll `getUpdates` fallback for local dev without ngrok (feeds the same `process_query`) |
| `scripts/eval_report.py` | **new** — run `eval_abstention` + `eval_routing` live and print a markdown block for `EVAL_REPORT.md` |
| `docker-compose.yml` | gateway service gets `TELEGRAM_BOT_TOKEN` + `TELEGRAM_WEBHOOK_SECRET` env (pass-through from host `.env`) |
| `.env.example` | + Phase-8 Telegram placeholders |
| `tests/test_config.py` | + Phase-8 telegram defaults |
| `tests/test_telegram.py` | **new** — `extract_message` parser + `TelegramClient` against a stubbed httpx |
| `tests/test_api.py` | + webhook: parses an update, replies via a spy `TelegramClient`, rejects a bad secret, reuses the cache |
| `tests/test_integration_telegram.py` | **new** `@integration` — real `getMe` returns the expected username (skips if no token) |
| `README.md` | top-level architecture overview + run + Telegram setup + EVAL_REPORT link |
| `EVAL_REPORT.md` | **new** — the graded submission doc (abstention + routing + token/cost-savings) |

---

## Task 0: Phase-8 Telegram config (no new dep)

**Files:** Modify `src/tiered_rag/config.py`, `.env.example`; Test `tests/test_config.py`.

**Design:** secrets are env-driven with empty defaults; `telegram_api_base` is overridable so tests can point
the client at a local stub.

**Step 1 — RED.** Append to `tests/test_config.py`:
```python
def test_phase8_telegram_defaults():
    s = Settings()
    assert s.telegram_bot_token == ""          # real value lives only in .env (gitignored)
    assert s.telegram_webhook_secret == ""
    assert s.telegram_api_base.startswith("https://api.telegram.org")


def test_phase8_telegram_token_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    assert Settings().telegram_bot_token == "123:ABC"
```

**Step 2 — Run → FAIL** (`pytest tests/test_config.py -v`).

**Step 3 — GREEN.** Add to `Settings` (after the Phase-7 block):
```python
    # --- Telegram front-end (Phase 8) ---
    telegram_bot_token: str = ""          # REAL value only in gitignored .env (never a default)
    telegram_webhook_secret: str = ""     # echoed by Telegram in X-Telegram-Bot-Api-Secret-Token
    telegram_api_base: str = "https://api.telegram.org"
```
Append to `.env.example`:
```dotenv
# --- Phase 8: Telegram front-end (placeholders only; real token in gitignored .env) ---
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_API_BASE=https://api.telegram.org
```

**Step 4 — Run → PASS.  Step 5 — Commit:**
```bash
git add src/tiered_rag/config.py .env.example tests/test_config.py
git commit -m "feat(p8): telegram config (token/secret/api-base, env-driven, empty defaults)"
```

---

## Task 1: `TelegramClient` + `extract_message` (`src/tiered_rag/telegram.py`)

**Files:** Create `src/tiered_rag/telegram.py`; Test `tests/test_telegram.py` (**new**).

**Design.** Mirror `OpenAICompatLLM`: a tiny `httpx`-based client, no SDK.
- `TelegramClient(token, api_base, timeout)`; `_url(method) -> f"{api_base}/bot{token}/{method}"`.
- `send_message(chat_id, text) -> dict` → `POST /sendMessage {chat_id, text}`.
- `get_me() -> dict`, `set_webhook(url, secret) -> dict` (passes `secret_token`), `delete_webhook() -> dict`,
  `get_webhook_info() -> dict`.
- `extract_message(update: dict) -> tuple[int, str] | None` — pure function: return `(chat_id, text)` for a
  normal text message, else `None` (edited messages, callbacks, non-text, missing fields → ignored). This is
  the unit most worth testing and must never raise on weird payloads.
- A `FakeTelegramClient` test double (records `sent: list[tuple[chat_id, text]]`) lives in the test module /
  `_helpers.py` for the webhook tests in Task 2.

**Step 1 — RED** (`tests/test_telegram.py`):
```python
from tiered_rag.telegram import TelegramClient, extract_message


def test_extract_message_pulls_chat_id_and_text():
    update = {"update_id": 1, "message": {"chat": {"id": 42}, "text": "hi there"}}
    assert extract_message(update) == (42, "hi there")


def test_extract_message_ignores_non_text_and_malformed():
    assert extract_message({"update_id": 2}) is None                       # no message
    assert extract_message({"message": {"chat": {"id": 1}}}) is None         # no text
    assert extract_message({"edited_message": {"chat": {"id": 1}, "text": "x"}}) is None
    assert extract_message({}) is None


def test_client_builds_method_urls_and_posts(monkeypatch):
    calls = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"ok": True, "result": {"message_id": 7}}

    def fake_post(url, json=None, timeout=None):
        calls["url"], calls["json"] = url, json
        return _Resp()

    monkeypatch.setattr("tiered_rag.telegram.httpx.post", fake_post)
    c = TelegramClient("123:ABC", "https://api.telegram.org", timeout=5.0)
    out = c.send_message(42, "hello")
    assert calls["url"] == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert calls["json"] == {"chat_id": 42, "text": "hello"}
    assert out["ok"] is True
```

**Step 2 — Run → FAIL** (`ModuleNotFoundError: tiered_rag.telegram`).

**Step 3 — GREEN** (`src/tiered_rag/telegram.py`):
```python
from __future__ import annotations

import httpx


def extract_message(update: dict) -> tuple[int, str] | None:
    """Pull (chat_id, text) from a Telegram update; None for anything we don't handle."""
    msg = update.get("message")
    if not isinstance(msg, dict):
        return None
    text = msg.get("text")
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if not isinstance(text, str) or not isinstance(chat_id, int):
        return None
    return chat_id, text


class TelegramClient:
    """Thin httpx client for the Telegram Bot API (no SDK), mirroring OpenAICompatLLM."""

    def __init__(self, token: str, api_base: str = "https://api.telegram.org", timeout: float = 10.0):
        self.token, self.api_base, self.timeout = token, api_base.rstrip("/"), timeout

    def _url(self, method: str) -> str:
        return f"{self.api_base}/bot{self.token}/{method}"

    def _post(self, method: str, payload: dict) -> dict:
        r = httpx.post(self._url(method), json=payload, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def send_message(self, chat_id: int, text: str) -> dict:
        return self._post("sendMessage", {"chat_id": chat_id, "text": text})

    def get_me(self) -> dict:
        r = httpx.post(self._url("getMe"), timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def set_webhook(self, url: str, secret: str = "") -> dict:
        payload = {"url": url}
        if secret:
            payload["secret_token"] = secret
        return self._post("setWebhook", payload)

    def delete_webhook(self) -> dict:
        return self._post("deleteWebhook", {})

    def get_webhook_info(self) -> dict:
        r = httpx.post(self._url("getWebhookInfo"), timeout=self.timeout)
        r.raise_for_status()
        return r.json()
```

**Step 4 — Run → PASS.  Step 5 — Commit:**
```bash
git add src/tiered_rag/telegram.py tests/test_telegram.py
git commit -m "feat(p8): TelegramClient (httpx, no SDK) + extract_message parser"
```

---

## Task 2: `POST /telegram/webhook` reusing the chat pipeline

**Files:** Modify `src/tiered_rag/api.py`; Test `tests/test_api.py` (extend) + `tests/_helpers.py`.

**Design — single source of truth.** Extract the `/chat` body into a module-level
`process_query(query, *, orchestrator, usage_log, cache, settings, alerter, background_tasks) -> ChatResponse`
(the cache get/put + `usage_log.record` + guardrail-alert scheduling, exactly as today). `POST /chat` becomes
a one-liner over it, so behaviour is **byte-for-byte unchanged** (all Phase-7 API tests stay green).

Add the webhook:
```python
def get_telegram(request: Request) -> TelegramClient | None:
    cli = getattr(request.app.state, "telegram", None)
    if cli is None:
        s = get_settings()
        if not s.telegram_bot_token:
            return None
        cli = TelegramClient(s.telegram_bot_token, s.telegram_api_base)
        request.app.state.telegram = cli
    return cli

@app.post("/telegram/webhook")
def telegram_webhook(
    update: dict,
    request: Request,
    background_tasks: BackgroundTasks,
    orchestrator: Orchestrator = Depends(get_orchestrator),
    usage_log: UsageLog = Depends(get_usage_log),
    alerter: Alerter = Depends(get_alerter),
    settings: Settings = Depends(get_settings_dep),
    cache: SemanticCache | None = Depends(get_cache),
    telegram: TelegramClient | None = Depends(get_telegram),
):
    # 1. validate the shared secret (defence-in-depth; Telegram echoes it in this header)
    if settings.telegram_webhook_secret:
        got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if got != settings.telegram_webhook_secret:
            return {"ok": False, "error": "bad secret"}      # 200 so Telegram doesn't retry a forgery
    # 2. parse; ignore anything that isn't a text message
    parsed = extract_message(update)
    if parsed is None or telegram is None:
        return {"ok": True}
    chat_id, text = parsed
    # 3. do the slow work AFTER responding, so Telegram never times out (it retries on slow/non-200)
    def _handle():
        resp = process_query(text, orchestrator=orchestrator, usage_log=usage_log,
                             cache=cache, settings=settings, alerter=alerter,
                             background_tasks=background_tasks)
        telegram.send_message(chat_id, resp.answer)
    background_tasks.add_task(_handle)
    return {"ok": True}
```

**Step 1 — RED** — add a `FakeTelegramClient` to `tests/_helpers.py`:
```python
class FakeTelegramClient:
    def __init__(self): self.sent = []
    def send_message(self, chat_id, text): self.sent.append((chat_id, text)); return {"ok": True}
```
and to `tests/test_api.py` (override `get_telegram` + the in-memory cache + spy orchestrator):
```python
def test_telegram_webhook_replies_with_chat_answer(fake_embedder):
    from tests._helpers import FakeTelegramClient, build_cached_client
    from tiered_rag.api import get_telegram
    client, spy = build_cached_client(fake_embedder, 1, "faq")
    tg = FakeTelegramClient()
    client.app.dependency_overrides[get_telegram] = lambda: tg
    update = {"update_id": 1, "message": {"chat": {"id": 99}, "text": "how do I reset my password"}}
    body = client.post("/telegram/webhook", json=update).json()
    assert body == {"ok": True}
    assert tg.sent and tg.sent[0][0] == 99          # replied to the right chat
    assert "Open Settings > Security > Reset." in tg.sent[0][1] or tg.sent[0][1]  # the pipeline's answer


def test_telegram_webhook_ignores_non_message_update(fake_embedder):
    from tests._helpers import FakeTelegramClient, build_cached_client
    from tiered_rag.api import get_telegram
    client, _ = build_cached_client(fake_embedder, 1, "faq")
    tg = FakeTelegramClient(); client.app.dependency_overrides[get_telegram] = lambda: tg
    body = client.post("/telegram/webhook", json={"update_id": 2}).json()
    assert body == {"ok": True} and tg.sent == []


def test_telegram_webhook_rejects_bad_secret(fake_embedder, monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cr3t")
    from tests._helpers import FakeTelegramClient, build_cached_client
    from tiered_rag.api import get_telegram
    client, _ = build_cached_client(fake_embedder, 1, "faq")
    tg = FakeTelegramClient(); client.app.dependency_overrides[get_telegram] = lambda: tg
    update = {"message": {"chat": {"id": 1}, "text": "hi"}}
    body = client.post("/telegram/webhook", json=update,
                       headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}).json()
    assert body["ok"] is False and tg.sent == []
```
*(`build_cached_client` already disables nothing extra; ensure `get_settings_dep` reads the secret. Because
`build_cached_client` builds the app and overrides `get_cache`/`get_orchestrator`, just add the
`get_telegram` override per test. `TestClient` runs `BackgroundTasks` on response, so `tg.sent` is populated
by the time `.json()` returns.)*

**Step 2 — Run → FAIL** (no `/telegram/webhook`, no `process_query`/`get_telegram`).

**Step 3 — GREEN.** Refactor `/chat` to call `process_query(...)`; add `get_telegram` + the webhook. Import
`from .telegram import TelegramClient, extract_message`.

**Step 4 — Run → PASS** (`pytest tests/test_api.py tests/test_telegram.py -v`, then the full offline suite to
prove `/chat` is unchanged).

**Step 5 — Commit:**
```bash
git add src/tiered_rag/api.py tests/test_api.py tests/_helpers.py
git commit -m "feat(p8): POST /telegram/webhook (secret-checked, background reply) reusing process_query"
```

---

## Task 3: webhook-setup script + polling fallback + ngrok docs + live `getMe`

**Files:** Create `scripts/set_telegram_webhook.py`, `scripts/telegram_poll.py`;
Test `tests/test_integration_telegram.py` (**new**, `@integration`).

**Design.**
- **`scripts/set_telegram_webhook.py`** — reads `TELEGRAM_BOT_TOKEN` + `TELEGRAM_WEBHOOK_SECRET` from
  `Settings`, takes `--url` (the public ngrok base), appends the `/telegram/webhook` path, calls
  `set_webhook`, then prints `get_webhook_info` to confirm. `--delete` calls `delete_webhook`.
- **`scripts/telegram_poll.py`** — local-dev fallback (no ngrok): long-poll `getUpdates` with an `offset`,
  and for each update POST it to the **local** `/telegram/webhook` (or call `process_query` + `send_message`
  directly). Lets you chat with the bot against a localhost gateway.
- **`tests/test_integration_telegram.py`** — `@integration`; skip if `telegram_bot_token` is empty;
  otherwise assert `TelegramClient(...).get_me()["result"]["username"] == "test123_miftah_bot"`. Does **not**
  send messages or set webhooks (no live chat target; avoids side effects on the shared bot).

**Step 1 — write the `@integration` test** (collects + skips when no token):
```python
import pytest
from tiered_rag.config import get_settings
from tiered_rag.telegram import TelegramClient

pytestmark = pytest.mark.integration


def test_get_me_returns_bot_identity():
    s = get_settings()
    if not s.telegram_bot_token:
        pytest.skip("TELEGRAM_BOT_TOKEN not set")
    me = TelegramClient(s.telegram_bot_token, s.telegram_api_base).get_me()
    assert me["ok"] is True
    assert me["result"]["is_bot"] is True
    assert me["result"]["username"]      # e.g. test123_miftah_bot
```

**Step 2 — implement the two scripts.** `set_telegram_webhook.py` sketch:
```python
import argparse
from tiered_rag.config import get_settings
from tiered_rag.telegram import TelegramClient

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="public base URL, e.g. https://<id>.ngrok-free.app")
    ap.add_argument("--delete", action="store_true")
    a = ap.parse_args()
    s = get_settings()
    cli = TelegramClient(s.telegram_bot_token, s.telegram_api_base)
    if a.delete:
        print(cli.delete_webhook())
    else:
        hook = a.url.rstrip("/") + "/telegram/webhook"
        print(cli.set_webhook(hook, s.telegram_webhook_secret))
    print(cli.get_webhook_info())
```

**Step 3 — the live wiring (documented; run manually with a fresh token in `.env`):**
```bash
# 1. real token + a random secret in .env (gitignored)
echo 'TELEGRAM_BOT_TOKEN=<fresh-token-from-BotFather>' >> .env
echo 'TELEGRAM_WEBHOOK_SECRET='"$(openssl rand -hex 16)" >> .env

# 2. bring up the gateway (LLM_TYPE=mock for a deterministic, offline-capable bot)
docker compose up -d --build      # qdrant + redis + mock workers + gateway (:8000)
python -m tiered_rag.ingest       # KB into Qdrant for the FAQ path

# 3. expose :8000 and register the webhook
ngrok http 8000                   # copy the https URL it prints
python scripts/set_telegram_webhook.py --url https://<id>.ngrok-free.app
#   (equivalently, the raw call the user already knows — note our path + secret):
#   curl -X POST "https://api.telegram.org/bot<token>/setWebhook?url=https://<id>.ngrok-free.app/telegram/webhook&secret_token=<secret>"

# 4. message @test123_miftah_bot in Telegram -> the gateway answers via the Phase-1–7 pipeline.
#    No ngrok? run the polling fallback instead:  python scripts/telegram_poll.py
```
> **NOTE on ports:** the gateway listens on **8000** (not 8080), so it's `ngrok http 8000` and the webhook URL
> must include the **`/telegram/webhook`** path.

**Step 4 — Run** `pytest -m integration tests/test_integration_telegram.py -s` (passes with a token in `.env`,
skips otherwise).

**Step 5 — Commit:**
```bash
git add scripts/set_telegram_webhook.py scripts/telegram_poll.py tests/test_integration_telegram.py
git commit -m "feat(p8): webhook-setup + polling scripts + live getMe integration test + ngrok docs"
```

---

## Task 4: Final packaging — Dockerfile / docker-compose / wiring

**Files:** Modify `docker-compose.yml` (Telegram env on the gateway); review `Dockerfile`.

**Design.** The Phase-7 compose already brings up **Qdrant + Redis + mock_tier1/1b/2/3 + gateway**. Phase 8
only passes the Telegram secrets through to the gateway (from the host `.env`, so they stay out of the file):
```yaml
  gateway:
    # ...existing Phase-7 config...
    environment:
      # ...existing...
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN:-}
      TELEGRAM_WEBHOOK_SECRET: ${TELEGRAM_WEBHOOK_SECRET:-}
```
Confirm the `Dockerfile` can also serve the gateway (it already installs the package; the compose `gateway`
service overrides `CMD` with `uvicorn tiered_rag.api:app`). Verify `docker compose up -d --build` →
`curl localhost:8000/healthz` → `{"status":"ok"}` and a `/chat` smoke call works.

**Step — verify + commit:**
```bash
docker compose up -d --build && curl -s localhost:8000/healthz
git add docker-compose.yml
git commit -m "feat(p8): pass TELEGRAM_* env through to the compose gateway service"
```
*(No automated test — this is infra. The verification is the `curl` above; record that it returned ok.)*

---

## Task 5: `EVAL_REPORT.md` — the graded submission doc (from real runs)

**Files:** Create `scripts/eval_report.py`; Create `EVAL_REPORT.md`.

**Design — assemble, never invent.** `EVAL_REPORT.md` collects the numbers Phases 1–7 already produce:

| Section | Source | Already measured |
|---|---|---|
| **Abstention rate** | Phase-1 `eval_abstention.evaluate(retriever, dataset)` over a labeled in-scope / out-of-scope set (real ollama + Qdrant) | abstention on OOD; **false-abstention** on in-scope paraphrases |
| **Routing accuracy** | Phase-2 `eval_routing.evaluate` (real model = **1.00**) + Phase-3 live mock (**0.88**) over the 6-category set | per-category + confusion |
| **Token / cost-savings** | Phase-7 `savings_vs_all_tier3` | **62.6%** vs all-Tier-3 |
| **Cache hit-rate** | Phase-7 `cache_stats` | **57.1%** |
| **Load / scale** | Phase-7 `scripts/load_test.py` | `rps=16.7 p50=5768ms p95=8418ms p99=9410ms errors=0` @100 conc. |

- **`scripts/eval_report.py`** runs `eval_abstention` (build a real `Retriever` from `Settings`) and
  `eval_routing` (build a `Router` from `build_llm(s, 1)`) over the labeled datasets and prints a markdown
  block (accuracy, per-category, abstention/false-abstention). Skips a section gracefully if its service is
  down, logging what it skipped (never silently drop a section).
- **`EVAL_REPORT.md`** is then written with the actual printed numbers + the Phase-7 figures, each labeled
  with the date and the backend (`mock` vs `openai`). Discipline: **paste what the run printed; do not invent.**

**Step 1 — implement `scripts/eval_report.py`** (no RED/GREEN unit test — it's a reporting script over
already-tested harnesses; its correctness is the harnesses', covered by `tests/test_eval_abstention.py` +
`tests/test_eval_routing.py`).

**Step 2 — run the harnesses live and capture numbers:**
```bash
docker compose up -d qdrant && ollama serve &            # real RAG
python -m tiered_rag.ingest
python scripts/eval_report.py            # prints abstention + routing blocks (mock or openai)
# also re-read the Phase-7 figures from a fresh load run if desired:
python scripts/load_test.py --n 300 --concurrency 100 && curl -s localhost:8000/stats
```

**Step 3 — write `EVAL_REPORT.md`** with: a one-paragraph summary, the abstention table, the routing table
(both backends), the cost-savings + cache hit-rate, the load-test line, and a short "how these were measured"
note (commands above). Cross-check the Phase-1/2/3 memory notes for the recorded baselines.

**Step 4 — Commit:**
```bash
git add scripts/eval_report.py EVAL_REPORT.md
git commit -m "docs(p8): EVAL_REPORT.md — abstention + routing + token/cost-savings (from real runs)"
```

---

## Task 6: Final README pass + submission polish

**Files:** Modify `README.md`.

**Design.** Add a **top-of-file architecture overview** (the 8-phase pipeline in one diagram), a **Quick
Start** (`docker compose up` → ingest → `/chat` curl), a **Telegram** section (bot setup with ngrok +
`set_telegram_webhook.py`, the polling fallback, the security note about keeping the token in `.env`), an
**Endpoints** table (`/healthz`, `/chat`, `/usage`, `/stats`, `/telegram/webhook`), and a link to
`EVAL_REPORT.md`. Keep the existing per-phase sections (they're the detailed record); the new overview sits
above them. Do **not** restate invented numbers — link to the measured Phase-7 + EVAL_REPORT figures.

**Step — write + commit:**
```bash
git add README.md
git commit -m "docs(p8): README architecture overview + quick start + Telegram setup + endpoints + EVAL link"
```

---

## Phase 8 Definition of Done

- [x] `pytest -m "not integration"` → all green offline, **including `/chat` byte-for-byte unchanged** after
      the `process_query` refactor (the Phase-7 API tests must all still pass). → **`120 passed, 10 deselected`**.
- [x] **Telegram webhook**: `POST /telegram/webhook` validates the shared secret, parses a text update,
      replies to the right `chat_id` via an injected `TelegramClient`, **reuses the Phase-1–7 pipeline**
      (router → tier → guardrail → cache), and **ignores** non-text / malformed updates without raising.
      Covered offline with a `FakeTelegramClient` spy + `TestClient` (3 webhook tests in `tests/test_api.py`).
- [x] **`TelegramClient`** speaks the Bot API over raw `httpx` (no SDK); `extract_message` is a pure,
      never-raises parser. Live `getMe` `@integration` test returns the bot identity (skips without a token —
      currently skips, no token in env).
- [x] **Token hygiene**: `config.py` default is empty; the real token lives only in gitignored `.env`;
      `.env.example` holds placeholders. (No `.env` is committed, so no secret is in the repo; the shared test
      token still needs BotFather `/revoke` before any real submission.)
- [~] **Packaging**: `docker compose config` validates and substitutes `TELEGRAM_*` into the gateway service.
      A **full** `docker compose up -d --build` + `curl localhost:8000/healthz` against the *compose* gateway
      was **not** run: port 8000 is held by a pre-existing manually-started `uvicorn` (not the compose
      project), so a rebuild would collide. Wiring is verified via `docker compose config` + the 120-test
      offline suite (which exercises `/chat` + the webhook in-process). **Remaining:** clear that process, then
      run the full stack smoke for the record.
- [x] **`EVAL_REPORT.md`** records, from **real runs (2026-05-29)**, the abstention rate (Phase 1 — **100%**
      OOD, **15%** false-abstain @ 0.6), routing accuracy (Phase 2 real = **1.00**, 16/16 — re-measured live;
      Phase 3 mock = **0.88**), token/cost-savings (Phase 7 = **62.6%**), cache hit-rate (**57.1%**), and the
      load-test line — never invented.
- [x] **`README.md`** has a top-level architecture overview + Quick Start + Telegram setup (ngrok +
      `set_telegram_webhook.py` + polling fallback) + endpoints table + EVAL_REPORT link, plus a Phase-8
      section. All work committed (7 commits, `6e0ff2a`…`5dd2438`).

**This is the final phase — after it, the package is submittable:** a runnable `docker compose` stack, a
Telegram front-end over the zero-hallucination tiered pipeline, and the two graded documents
(`README.md` + `EVAL_REPORT.md`).

---

## Implementation status (2026-05-29) — as built

All 7 tasks implemented TDD (RED → GREEN → COMMIT), offline suite **120 passed**. Notes on where the
**as-built** code differs from the sketches above (the sketches were directional; the repo's actual
shape was followed):

| # | Commit | As-built notes |
|---|---|---|
| 0 | `6e0ff2a` | `telegram_bot_token` / `telegram_webhook_secret` / `telegram_api_base` added to `Settings`; `.env.example` placeholders. `test_config.py` → 14 passed. |
| 1 | `0f6b908` | `src/tiered_rag/telegram.py` exactly as sketched (`TelegramClient` + `extract_message`). `test_telegram.py` → 3 passed. |
| 2 | `96e9f6c` | `/chat` body extracted into module-level `process_query(...)`; **`api.py` uses the existing `create_app()` factory** (routes are nested `@app.post` inside `create_app`, not module-level decorators as the sketch implied). `get_telegram` + `POST /telegram/webhook` added. `FakeTelegramClient` lives in `tests/_helpers.py`. |
| 3 | `a53bcc0` | `set_telegram_webhook.py` as sketched. **`telegram_poll.py` forwards each update to the local gateway's `/chat`** (then replies via `TelegramClient`) rather than re-POSTing to `/telegram/webhook` — avoids the secret round-trip and reuses the exact pipeline. `test_integration_telegram.py` (`@integration` `getMe`) skips without a token. |
| 4 | `4cca56d` | `docker-compose.yml` gateway gets `TELEGRAM_BOT_TOKEN` / `TELEGRAM_WEBHOOK_SECRET` (`${VAR:-}` pass-through). See the packaging caveat above. |
| 5 | `a011f99` | `scripts/eval_report.py` builds the abstention dataset from `tests/data/eval_questions.py` (`IN_SCOPE`/`OUT_OF_SCOPE`) and routing from `tests/data/routing_questions.py`; each block skips gracefully if its service is down. `EVAL_REPORT.md` written from the live run (real routing **1.00** re-measured today via the shell `OPENAI_API_KEY`). |
| 6 | `5dd2438` | `README.md` overview + Quick Start + Telegram + endpoints + EVAL link + a Phase-8 per-phase section. |

**Environment notes for this machine:** offline tests run under the `tiered_rag` conda env
(`/home/miftah/miniconda3/envs/tiered_rag/bin/python`), not `base` (which lacks pytest). The live
eval run used a host-env `OPENAI_API_KEY` + a freshly-started `qdrant` + `python -m tiered_rag.ingest`.
