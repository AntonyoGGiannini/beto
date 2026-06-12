"""A oportunidade de exemplo (alerta de teste do Telegram) é uma surebet real."""

from __future__ import annotations

import pytest

from beto.alerting.formatting import format_alert_html
from beto.models import MarketType
from beto.ui.demo import demo_opportunity


def test_demo_opportunity_e_uma_surebet():
    opp = demo_opportunity(bankroll=1000.0)
    assert opp.market_type is MarketType.MATCH_1X2
    assert opp.profit_pct == pytest.approx(0.80, abs=1e-9)
    assert {leg.house for leg in opp.legs} == {"betano", "novibet"}


def test_demo_opportunity_formata_para_telegram():
    html = format_alert_html(demo_opportunity())
    assert html.startswith("<pre>") and html.endswith("</pre>")
    assert "SUREBET" in html
