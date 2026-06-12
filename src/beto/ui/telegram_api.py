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


def _raise_friendly(exc: httpx.HTTPStatusError) -> None:
    """Converte erros comuns da Bot API em mensagens legíveis para o usuário."""
    code = exc.response.status_code
    try:
        detail = exc.response.json().get("description", "")
    except Exception:  # noqa: BLE001
        detail = exc.response.text[:200]
    if code == 400:
        raise RuntimeError(
            f"400 Bad Request: {detail}\n\n"
            "👉 O bot ainda não conhece este chat. Abra o Telegram, procure seu bot "
            "pelo username e envie /start. Depois use 🔎 Descobrir chat_id."
        ) from exc
    if code == 401:
        raise RuntimeError(
            "401 Unauthorized: token inválido ou revogado — gere um novo no @BotFather."
        ) from exc
    if code == 403:
        raise RuntimeError(
            f"403 Forbidden: {detail} — o bot foi bloqueado pelo usuário ou o chat_id "
            "é de um canal/grupo que o bot não pode enviar mensagens."
        ) from exc
    raise RuntimeError(f"Telegram API {code}: {detail}") from exc


def discover_chats(token: str, *, timeout: float = 10.0) -> list[dict[str, Any]]:
    """Lista conversas recentes do bot (via getUpdates) para descobrir o chat_id.

    O usuário precisa ter enviado ao menos uma mensagem ao bot antes.
    """
    try:
        resp = httpx.get(f"{_API}/bot{token}/getUpdates", timeout=timeout)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        _raise_friendly(exc)
    chats: dict[Any, dict[str, Any]] = {}
    for update in resp.json().get("result", []):
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat")
        if chat and "id" in chat:
            chats[chat["id"]] = chat
    return list(chats.values())


def send_text(token: str, chat_id: str, text: str, *, timeout: float = 10.0) -> None:
    try:
        resp = httpx.post(
            f"{_API}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=timeout,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        _raise_friendly(exc)


def send_alert(token: str, chat_id: str, opp: ArbOpportunity, *, timeout: float = 10.0) -> None:
    """Envia uma surebet já formatada (mesmo HTML dos alertas de produção)."""
    try:
        resp = httpx.post(
            f"{_API}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": format_alert_html(opp), "parse_mode": "HTML"},
            timeout=timeout,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        _raise_friendly(exc)
