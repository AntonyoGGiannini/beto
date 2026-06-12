"""Alertas via Telegram (python-telegram-bot v21, async) + comandos /status /threshold.

Configuração: BETO_TELEGRAM_BOT_TOKEN (crie no @BotFather) e BETO_TELEGRAM_CHAT_ID
(descubra com @userinfobot). Os comandos respondem apenas no chat configurado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from beto.alerting.formatting import format_alert_html
from beto.arbitrage.engine import ArbOpportunity

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class RuntimeState:
    """Estado compartilhado entre o loop de monitoramento e os comandos do bot."""

    min_profit_pct: float
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_cycle_summary: str = "nenhum ciclo concluído ainda"


class TelegramAlerter:
    name = "telegram"

    def __init__(self, token: str, chat_id: str) -> None:
        from telegram import Bot  # import tardio: dependência pesada

        self._bot = Bot(token)
        self._chat_id = chat_id

    async def send_alert(self, opp: ArbOpportunity) -> None:
        await self._bot.send_message(
            chat_id=self._chat_id, text=format_alert_html(opp), parse_mode="HTML"
        )

    async def send_text(self, text: str) -> None:
        await self._bot.send_message(chat_id=self._chat_id, text=text)

    async def aclose(self) -> None:
        return None


class CommandBot:
    """Bot de comandos rodando em paralelo ao loop: /status e /threshold [pct]."""

    def __init__(self, token: str, chat_id: str, state: RuntimeState, repo: Any) -> None:
        self._token = token
        self._chat_id = str(chat_id)
        self._state = state
        self._repo = repo
        self._app: Any = None

    def _authorized(self, update: Any) -> bool:
        chat = update.effective_chat
        return chat is not None and str(chat.id) == self._chat_id

    async def _cmd_status(self, update: Any, _context: Any) -> None:
        if not self._authorized(update):
            return
        uptime = datetime.now(UTC) - self._state.started_at
        text = (
            f"⏳ no ar há {str(uptime).split('.')[0]}\n"
            f"📈 threshold: {self._state.min_profit_pct:.2f}%\n"
            f"🔁 último ciclo: {self._state.last_cycle_summary}\n"
            f"💰 surebets nas últimas 24h: {self._repo.opportunities_today()}"
        )
        await update.message.reply_text(text)

    async def _cmd_threshold(self, update: Any, context: Any) -> None:
        if not self._authorized(update):
            return
        if context.args:
            try:
                value = float(context.args[0].replace(",", "."))
            except ValueError:
                await update.message.reply_text("Uso: /threshold 0.8")
                return
            self._state.min_profit_pct = value
            await update.message.reply_text(f"Threshold ajustado para {value:.2f}%")
        else:
            await update.message.reply_text(
                f"Threshold atual: {self._state.min_profit_pct:.2f}% — mude com /threshold 0.8"
            )

    async def start(self) -> None:
        from telegram.ext import Application, CommandHandler

        self._app = Application.builder().token(self._token).build()
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("threshold", self._cmd_threshold))
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        log.info("telegram.commands_started")

    async def stop(self) -> None:
        if self._app is None:
            return
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()
