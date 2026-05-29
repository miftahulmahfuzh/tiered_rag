"""Register (or delete) the Telegram webhook for the gateway (Phase 8).

Reads TELEGRAM_BOT_TOKEN + TELEGRAM_WEBHOOK_SECRET from Settings (i.e. the gitignored .env).
Takes the public base URL (e.g. an ngrok https URL), appends the /telegram/webhook path,
calls setWebhook with the shared secret, then prints getWebhookInfo to confirm.

Usage:
    python scripts/set_telegram_webhook.py --url https://<id>.ngrok-free.app
    python scripts/set_telegram_webhook.py --delete
"""
import argparse

from tiered_rag.config import get_settings
from tiered_rag.telegram import TelegramClient


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="public base URL, e.g. https://<id>.ngrok-free.app")
    ap.add_argument("--delete", action="store_true", help="remove the webhook instead of setting it")
    a = ap.parse_args()

    s = get_settings()
    if not s.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set (put it in the gitignored .env)")
    cli = TelegramClient(s.telegram_bot_token, s.telegram_api_base)

    if a.delete:
        print("deleteWebhook ->", cli.delete_webhook())
    else:
        if not a.url:
            raise SystemExit("--url is required (or pass --delete)")
        hook = a.url.rstrip("/") + "/telegram/webhook"
        print("setWebhook ->", cli.set_webhook(hook, s.telegram_webhook_secret))
    print("getWebhookInfo ->", cli.get_webhook_info())


if __name__ == "__main__":
    main()
