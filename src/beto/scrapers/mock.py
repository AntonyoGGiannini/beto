"""Casa fictícia para provar o pipeline ponta a ponta sem rede.

Emite quotes de DUAS "casas" simuladas (mock_alpha/mock_beta) contendo:
- uma surebet 1X2 conhecida (melhores odds 2.10/3.60/4.20 → lucro ~0,80%);
- um mercado O/U gols SEM arbitragem (ruído).

Habilite com `--houses mock` (ou BETO_ENABLED_HOUSES=mock).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from beto.models import MarketType, OddsQuote, Outcome, Sport
from beto.scrapers.base import BookmakerScraper


class MockScraper(BookmakerScraper):
    house = "mock"
    note = "dados fictícios (teste do pipeline)"

    async def scrape(self) -> list[OddsQuote]:
        now = datetime.now(UTC)
        kickoff = (now + timedelta(hours=6)).replace(minute=0, second=0, microsecond=0)

        def quote(
            house: str,
            market: MarketType,
            line: float | None,
            outcomes: tuple[Outcome, ...],
        ) -> OddsQuote:
            return OddsQuote(
                house=house,
                sport=Sport.FOOTBALL,
                league="Copa do Mundo 2026 (simulada)",
                home_team="Brasil",
                away_team="México",
                start_time=kickoff,
                market_type=market,
                line=line,
                outcomes=outcomes,
                scraped_at=now,
            )

        return [
            quote(
                "mock_alpha",
                MarketType.MATCH_1X2,
                None,
                (Outcome("home", 2.10), Outcome("draw", 3.10), Outcome("away", 3.50)),
            ),
            quote(
                "mock_beta",
                MarketType.MATCH_1X2,
                None,
                (Outcome("home", 1.95), Outcome("draw", 3.60), Outcome("away", 4.20)),
            ),
            quote(
                "mock_alpha",
                MarketType.OU_GOALS,
                2.5,
                (Outcome("over", 1.90), Outcome("under", 1.85)),
            ),
            quote(
                "mock_beta",
                MarketType.OU_GOALS,
                2.5,
                (Outcome("over", 1.88), Outcome("under", 1.92)),
            ),
        ]
