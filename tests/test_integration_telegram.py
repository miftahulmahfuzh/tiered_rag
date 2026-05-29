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
