"""Local-dev Telegram fallback (Phase 8): long-poll getUpdates, no ngrok needed.

Instead of a public webhook, this long-polls the Bot API's getUpdates with an advancing
offset and, for each text message, forwards it to the LOCAL gateway's /chat endpoint, then
replies via TelegramClient.send_message. Identical answers to the webhook path (both reuse
the Phase-1-7 pipeline) -- the only difference is the transport.

Note: getUpdates conflicts with an active webhook, so delete the webhook first:
    python scripts/set_telegram_webhook.py --delete

Usage:
    uvicorn tiered_rag.api:app --port 8000     # the gateway (LLM_TYPE=mock recommended)
    python -m tiered_rag.ingest                # KB into Qdrant for the FAQ path
    python scripts/telegram_poll.py --gateway http://localhost:8000
"""
import argparse

import httpx

from tiered_rag.config import get_settings
from tiered_rag.telegram import TelegramClient, extract_message


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", default="http://localhost:8000", help="local gateway base URL")
    ap.add_argument("--timeout", type=int, default=30, help="long-poll timeout (seconds)")
    a = ap.parse_args()

    s = get_settings()
    if not s.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set (put it in the gitignored .env)")
    cli = TelegramClient(s.telegram_bot_token, s.telegram_api_base)
    updates_url = f"{s.telegram_api_base.rstrip('/')}/bot{s.telegram_bot_token}/getUpdates"

    print(f"polling getUpdates -> forwarding to {a.gateway}/chat  (Ctrl-C to stop)")
    offset = None
    while True:
        params = {"timeout": a.timeout}
        if offset is not None:
            params["offset"] = offset
        r = httpx.post(updates_url, json=params, timeout=a.timeout + 5)
        r.raise_for_status()
        for upd in r.json().get("result", []):
            offset = upd["update_id"] + 1          # ack: never re-fetch this update
            parsed = extract_message(upd)
            if parsed is None:
                continue
            chat_id, text = parsed
            try:
                resp = httpx.post(f"{a.gateway.rstrip('/')}/chat", json={"query": text}, timeout=60)
                resp.raise_for_status()
                answer = resp.json()["answer"]
            except Exception as e:                 # keep polling even if the gateway hiccups
                answer = f"(gateway error: {e})"
            cli.send_message(chat_id, answer)
            print(f"  chat {chat_id}: {text!r} -> {answer[:60]!r}")


if __name__ == "__main__":
    main()
