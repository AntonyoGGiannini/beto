"""Formatação de alertas de surebet (texto puro p/ console; HTML p/ Telegram)."""

from __future__ import annotations

import html
from datetime import UTC, datetime

from beto.arbitrage.engine import ArbOpportunity
from beto.models import MarketType


def market_label(market_type: MarketType, line: float | None) -> str:
    if market_type is MarketType.MATCH_1X2:
        return "Resultado (1X2)"
    if market_type is MarketType.OU_GOALS:
        return f"Gols Mais/Menos {line}"
    return f"Escanteios Mais/Menos {line}"


def outcome_label(label: str, line: float | None) -> str:
    names = {"home": "Casa", "draw": "Empate", "away": "Fora"}
    if label in names:
        return names[label]
    if label == "over":
        return f"Mais {line}"
    if label == "under":
        return f"Menos {line}"
    return label


def _fmt_brl(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def format_alert_text(opp: ArbOpportunity) -> str:
    """Mensagem em texto puro (console e base do HTML)."""
    when = (
        opp.start_time.astimezone(UTC).strftime("%d/%m %H:%M UTC")
        if opp.start_time
        else "horário não informado"
    )
    lines = [
        f"🟢 SUREBET +{opp.profit_pct:.2f}%",
        f"⚽ {opp.home_team} x {opp.away_team}",
        f"🏆 {opp.league or 'competição não informada'} · {when}",
        f"Mercado: {market_label(opp.market_type, opp.line)}",
        "",
    ]
    width = max(len(outcome_label(leg.outcome_label, opp.line)) for leg in opp.legs)
    for leg in opp.legs:
        label = outcome_label(leg.outcome_label, opp.line).ljust(width)
        lines.append(
            f"• {label}  @ {leg.odds:.2f}  ({leg.house})  → {_fmt_brl(leg.stake)}"
        )
    guaranteed = opp.bankroll / opp.arb_sum
    lines += [
        "",
        f"Banca {_fmt_brl(opp.bankroll)} · retorno garantido ≈ {_fmt_brl(guaranteed)}",
        f"⏱ detectada {datetime.now(UTC).strftime('%H:%M:%S')} UTC — "
        "odds mudam rápido, confirme antes de apostar",
    ]
    return "\n".join(lines)


def format_alert_html(opp: ArbOpportunity) -> str:
    """Versão para Telegram (parse_mode=HTML)."""
    return f"<pre>{html.escape(format_alert_text(opp))}</pre>"
