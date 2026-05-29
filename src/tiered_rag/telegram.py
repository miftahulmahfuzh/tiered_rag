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
