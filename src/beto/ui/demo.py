"""Oportunidade de exemplo — alimenta o "alerta de teste" do Telegram na interface.

Reaproveita o motor real (matcher + arbitragem) para que o alerta de teste tenha
exatamente o mesmo formato dos alertas de produção.
"""

from __future__ import annotations

from datetime import UTC, datetime

from beto.arbitrage.engine import ArbOpportunity, find_arbitrage
from beto.matching.matcher import EventMatcher
from beto.models import MarketType, OddsQuote, Outcome, Sport

_KICKOFF = datetime(2026, 6, 13, 19, 0, tzinfo=UTC)


def _quote(house: str, outcomes: list[Outcome]) -> OddsQuote:
    return OddsQuote(
        house=house,
        sport=Sport.FOOTBALL,
        league="Copa do Mundo 2026",
        home_team="Brasil",
        away_team="México",
        start_time=_KICKOFF,
        market_type=MarketType.MATCH_1X2,
        line=None,
        outcomes=tuple(outcomes),
        scraped_at=datetime.now(UTC),
    )


def demo_opportunity(bankroll: float = 1000.0) -> ArbOpportunity:
    """Surebet 1X2 determinística (+0,80%) entre 'betano' e 'novibet'."""
    quotes = [
        _quote("betano", [Outcome("home", 2.10), Outcome("draw", 3.10), Outcome("away", 3.50)]),
        _quote("novibet", [Outcome("home", 1.95), Outcome("draw", 3.60), Outcome("away", 4.20)]),
    ]
    events = EventMatcher().group(quotes)
    opp = find_arbitrage(events[0], MarketType.MATCH_1X2, None, bankroll=bankroll)
    if opp is None:  # pragma: no cover — invariante do exemplo
        raise RuntimeError("exemplo deveria sempre produzir uma surebet")
    return opp
