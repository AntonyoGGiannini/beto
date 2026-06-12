"""Integração ponta a ponta com a casa mock: scrape → match → arbitragem."""

from __future__ import annotations

import pytest

from beto.arbitrage.engine import find_all
from beto.config import Settings
from beto.matching.matcher import EventMatcher
from beto.models import MarketType
from beto.scrapers.mock import MockScraper
from beto.scrapers.transport import Transport


async def test_mock_gera_a_surebet_conhecida():
    settings = Settings(telegram_bot_token=None, telegram_chat_id=None)
    transport = Transport(settings)  # mock não usa rede; só satisfaz a interface

    result = await MockScraper(transport).safe_scrape()
    assert result.ok
    assert len(result.quotes) == 4

    events = EventMatcher().group(result.quotes)
    assert len(events) == 1  # mesmo jogo nas duas "casas" simuladas

    opportunities = find_all(events, bankroll=1000.0, min_profit_pct=0.0)
    assert len(opportunities) == 1
    opp = opportunities[0]
    assert opp.market_type is MarketType.MATCH_1X2
    assert opp.profit_pct == pytest.approx(0.80, abs=1e-9)
    assert {leg.house for leg in opp.legs} == {"mock_alpha", "mock_beta"}
