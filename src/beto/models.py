"""Modelo de dados normalizado — o contrato entre scrapers, matching, arbitragem e alertas.

Todo adaptador de casa devolve apenas `OddsQuote`s; as camadas seguintes não conhecem
nenhum detalhe de site/HTML/JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Sport(StrEnum):
    FOOTBALL = "football"
    # depois: TENNIS, BASKETBALL, ...


class MarketType(StrEnum):
    MATCH_1X2 = "1x2"  # resultado final: home/draw/away
    OU_GOALS = "ou_goals"  # over/under gols — exige `line` (0.5, 1.5, 2.5...)
    OU_CORNERS = "ou_corners"  # over/under escanteios — exige `line` (8.5, 9.5...)


# Conjunto EXATO de rótulos canônicos que cada mercado precisa cobrir para haver
# arbitragem garantida. Um 1X2 sem o empate precificado não é arbitragem.
REQUIRED_LABELS: dict[MarketType, frozenset[str]] = {
    MarketType.MATCH_1X2: frozenset({"home", "draw", "away"}),
    MarketType.OU_GOALS: frozenset({"over", "under"}),
    MarketType.OU_CORNERS: frozenset({"over", "under"}),
}


@dataclass(frozen=True, slots=True)
class Outcome:
    """Um resultado apostável com sua odd decimal."""

    label: str  # canônico: home | draw | away | over | under
    decimal_odds: float


@dataclass(frozen=True, slots=True)
class OddsQuote:
    """UMA casa × UM mercado × UM evento, num instante.

    Agrupa todos os resultados do mercado num snapshot só — nunca misturamos odds
    coletadas em momentos diferentes da mesma casa.
    """

    house: str
    sport: Sport
    league: str | None
    home_team: str
    away_team: str
    start_time: datetime | None  # tz-aware UTC quando conhecido
    market_type: MarketType
    line: float | None  # linha de O/U; None para 1X2
    outcomes: tuple[Outcome, ...]
    scraped_at: datetime  # tz-aware UTC
    source_event_id: str | None = None
    url: str | None = None

    def outcome_labels(self) -> frozenset[str]:
        return frozenset(o.label for o in self.outcomes)

    def event_fingerprint(self) -> tuple[str, str, str]:
        """Identidade aproximada do evento dentro de UMA casa (contagem/relatório)."""
        day = self.start_time.date().isoformat() if self.start_time else "?"
        return (self.home_team.lower(), self.away_team.lower(), day)
