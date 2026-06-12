"""Fábricas compartilhadas pelos testes."""

from __future__ import annotations

from datetime import UTC, datetime

from beto.models import MarketType, OddsQuote, Outcome, Sport

KICKOFF = datetime(2026, 6, 13, 19, 0, tzinfo=UTC)


def make_quote(
    house: str,
    *,
    home: str = "Brasil",
    away: str = "México",
    market: MarketType = MarketType.MATCH_1X2,
    line: float | None = None,
    outcomes: tuple[Outcome, ...] | list[Outcome] = (),
    start: datetime | None = KICKOFF,
    league: str | None = "Copa do Mundo 2026",
) -> OddsQuote:
    return OddsQuote(
        house=house,
        sport=Sport.FOOTBALL,
        league=league,
        home_team=home,
        away_team=away,
        start_time=start,
        market_type=market,
        line=line,
        outcomes=tuple(outcomes),
        scraped_at=datetime.now(UTC),
    )
