"""Alerter de console: imprime o alerta no terminal (modo dry-run / sem credenciais)."""

from __future__ import annotations

from beto.alerting.formatting import format_alert_text
from beto.arbitrage.engine import ArbOpportunity


class ConsoleAlerter:
    name = "console"

    async def send_alert(self, opp: ArbOpportunity) -> None:
        print("\n" + "=" * 64)
        print(format_alert_text(opp))
        print("=" * 64)

    async def aclose(self) -> None:  # simetria com TelegramAlerter
        return None
