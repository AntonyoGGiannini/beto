"""Motor de arbitragem: melhores odds por resultado → soma dos inversos → stakes.

Matemática: para um mercado com N resultados mutuamente exclusivos, pegue a MELHOR
odd de cada resultado entre as casas. Se arb_sum = Σ 1/odd_i < 1, há lucro garantido
de (1/arb_sum − 1)·100 %. Stake ótima: stake_i = banca · (1/odd_i) / arb_sum, que
iguala o retorno em qualquer desfecho (retorno = banca / arb_sum).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from beto.matching.matcher import MatchedEvent
from beto.models import REQUIRED_LABELS, MarketType, Sport

# ordem de exibição dos rótulos nos alertas
_LABEL_ORDER = {"home": 0, "draw": 1, "away": 2, "over": 0, "under": 1}


@dataclass(frozen=True, slots=True)
class StakeAllocation:
    """Uma perna da arbitragem: onde apostar, quanto e o que isso devolve."""

    outcome_label: str
    house: str
    odds: float
    stake: float  # absoluto, dada a banca
    stake_pct: float  # fração da banca
    guaranteed_return: float  # stake × odds (igual em todas as pernas, por construção)


@dataclass(frozen=True, slots=True)
class ArbOpportunity:
    event_key: str
    sport: Sport
    home_team: str
    away_team: str
    league: str | None
    start_time: datetime | None
    market_type: MarketType
    line: float | None
    arb_sum: float
    profit_pct: float
    bankroll: float
    legs: tuple[StakeAllocation, ...]
    found_at: datetime

    def dedup_key(self) -> str:
        """Identidade da oportunidade p/ não re-alertar: evento + mercado +
        atribuição resultado→casa + odds arredondadas (2 casas)."""
        legs = ";".join(
            f"{leg.outcome_label}:{leg.house}:{leg.odds:.2f}"
            for leg in sorted(self.legs, key=lambda x: x.outcome_label)
        )
        raw = f"{self.event_key}|{self.market_type.value}|{self.line}|{legs}"
        return hashlib.sha1(raw.encode()).hexdigest()


def _norm_line(line: float | None) -> float | None:
    return None if line is None else round(line, 2)


def find_arbitrage(
    event: MatchedEvent,
    market_type: MarketType,
    line: float | None,
    *,
    bankroll: float,
    min_profit_pct: float = 0.0,
) -> ArbOpportunity | None:
    line = _norm_line(line)
    quotes = [
        q
        for q in event.quotes
        if q.market_type == market_type and _norm_line(q.line) == line
    ]
    if len(quotes) < 2:
        return None

    required = REQUIRED_LABELS[market_type]
    best: dict[str, tuple[str, float]] = {}  # label → (casa, melhor odd)
    for label in sorted(required, key=lambda lb: _LABEL_ORDER.get(lb, 9)):
        candidates = [
            (q.house, o.decimal_odds)
            for q in quotes
            for o in q.outcomes
            if o.label == label
        ]
        if not candidates:
            return None  # resultado sem preço em casa alguma → sem arbitragem
        best[label] = max(candidates, key=lambda c: c[1])

    # arb "dentro de uma casa só" é quase sempre bug de parsing — exigir ≥2 casas
    if len({house for house, _ in best.values()}) < 2:
        return None

    arb_sum = sum(1.0 / odds for _, odds in best.values())
    if arb_sum >= 1.0:
        return None
    profit_pct = (1.0 / arb_sum - 1.0) * 100.0
    if profit_pct < min_profit_pct:
        return None

    legs = []
    for label, (house, odds) in best.items():
        fraction = (1.0 / odds) / arb_sum
        stake = bankroll * fraction
        legs.append(
            StakeAllocation(
                outcome_label=label,
                house=house,
                odds=odds,
                stake=round(stake, 2),
                stake_pct=fraction,
                guaranteed_return=round(stake * odds, 2),
            )
        )

    return ArbOpportunity(
        event_key=event.key,
        sport=event.sport,
        home_team=event.home_team,
        away_team=event.away_team,
        league=event.league,
        start_time=event.start_time,
        market_type=market_type,
        line=line,
        arb_sum=arb_sum,
        profit_pct=profit_pct,
        bankroll=bankroll,
        legs=tuple(legs),
        found_at=datetime.now(UTC),
    )


def find_all(
    events: list[MatchedEvent], *, bankroll: float, min_profit_pct: float = 0.0
) -> list[ArbOpportunity]:
    """Roda o motor em todo (evento × mercado × linha) com ≥2 casas presentes."""
    opportunities: list[ArbOpportunity] = []
    for event in events:
        combos: dict[tuple[MarketType, float | None], set[str]] = {}
        for q in event.quotes:
            combos.setdefault((q.market_type, _norm_line(q.line)), set()).add(q.house)
        for (market_type, line), houses in combos.items():
            if len(houses) < 2:
                continue
            opp = find_arbitrage(
                event, market_type, line, bankroll=bankroll, min_profit_pct=min_profit_pct
            )
            if opp is not None:
                opportunities.append(opp)
    opportunities.sort(key=lambda o: o.profit_pct, reverse=True)
    return opportunities
