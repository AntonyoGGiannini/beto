"""Serialização do .env a partir da interface."""

from __future__ import annotations

from beto.config import Settings
from beto.ui.envfile import ALL_FIELDS, to_env_text


def test_to_env_text_formata_tipos():
    text = to_env_text(
        {
            "telegram_bot_token": "abc123",
            "telegram_chat_id": None,  # None → vazio
            "alerter": "telegram",
            "enabled_houses": "mock,betano",
            "min_profit_pct": 0.5,
            "headless": True,
            "log_json": False,
        }
    )
    assert text.startswith("# Gerado pela interface beto")
    assert "BETO_TELEGRAM_BOT_TOKEN=abc123" in text
    assert "BETO_TELEGRAM_CHAT_ID=\n" in text  # None vira string vazia
    assert "BETO_ENABLED_HOUSES=mock,betano" in text
    assert "BETO_MIN_PROFIT_PCT=0.5" in text
    assert "BETO_HEADLESS=true" in text  # bool minúsculo, compatível com pydantic
    assert "BETO_LOG_JSON=false" in text


def test_todos_os_campos_existem_no_settings():
    # garante que a interface não exponha um campo que o Settings não conhece
    s = Settings(_env_file=None)
    for field in ALL_FIELDS:
        assert hasattr(s, field), field
