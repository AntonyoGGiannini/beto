"""Formatação dos alertas (console e Telegram HTML)."""

from __future__ import annotations

from beto.alerting.formatting import format_alert_html, format_alert_text
from beto.arbitrage.engine import find_arbitrage
from beto.matching.matcher import EventMatcher
from beto.models import MarketType, Outcome
from helpers import make_quote


def _opportunity():
    events = EventMatcher().group(
        [
            make_quote(
                "betano",
                outcomes=[Outcome("home", 2.10), Outcome("draw", 3.10), Outcome("away", 3.50)],
            ),
            make_quote(
                "novibet",
                outcomes=[Outcome("home", 1.95), Outcome("draw", 3.60), Outcome("away", 4.20)],
            ),
        ]
    )
    return find_arbitrage(events[0], MarketType.MATCH_1X2, None, bankroll=1000.0)


def test_alerta_em_texto_tem_todos_os_dados():
    text = format_alert_text(_opportunity())
    assert "SUREBET +0.80%" in text
    assert "Brasil x México" in text
    assert "Copa do Mundo 2026" in text
    assert "Resultado (1X2)" in text
    assert "@ 2.10  (betano)" in text
    assert "@ 3.60  (novibet)" in text
    assert "R$ 480,00" in text  # stake da perna casa
    assert "R$ 1.008,00" in text  # retorno garantido
    assert "Casa" in text and "Empate" in text and "Fora" in text


def test_alerta_html_escapado_para_telegram():
    html = format_alert_html(_opportunity())
    assert html.startswith("<pre>") and html.endswith("</pre>")
