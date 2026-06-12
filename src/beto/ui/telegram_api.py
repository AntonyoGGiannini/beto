"""Chamadas síncronas à Bot API do Telegram — usadas só pela interface.

Mantém-se leve e síncrono (httpx) para encaixar no fluxo do Streamlit, sem subir o
`python-telegram-bot` async. O loop de produção (`beto run`) segue usando o
`TelegramAlerter`.
"""

from __future__ import annotations

from typing import Any

import httpx

from beto.alerting.formatting import format_alert_html
from beto.arbitrage.engine import ArbOpportunity

_API = "https://api.telegram.org"


def discover_chats(token: str, *, timeout: float = 10.0) -> list[dict[str, Any]]:
    """Lista conversas recentes do bot (via getUpdates) para descobrir o chat_id.

    O usuário precisa ter enviado ao menos uma mensagem ao bot antes.
    """
    resp = httpx.get(f"{_API}/bot{token}/getUpdates", timeout=timeout)
    resp.raise_for_status()
    chats: dict[Any, dict[str, Any]] = {}
    for update in resp.json().get("result", []):
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat")
        if chat and "id" in chat:
            chats[chat["id"]] = chat
    return list(chats.values())


def send_text(token: str, chat_id: str, text: str, *, timeout: float = 10.0) -> None:
    resp = httpx.post(
        f"{_API}/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=timeout,
    )
    resp.raise_for_status()


def send_alert(token: str, chat_id: str, opp: ArbOpportunity, *, timeout: float = 10.0) -> None:
    """Envia uma surebet já formatada (mesmo HTML dos alertas de produção)."""
    resp = httpx.post(
        f"{_API}/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": format_alert_html(opp), "parse_mode": "HTML"},
        timeout=timeout,
    )
    resp.raise_for_status()
