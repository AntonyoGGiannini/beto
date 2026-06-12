"""Agrupa quotes de casas diferentes que se referem AO MESMO jogo.

O problema de correção mais delicado do projeto: "São Paulo FC" vs "SAO PAULO",
"Brazil" vs "Brasil". Estratégia conservadora: normalização + alias + fuzzy
(rapidfuzz token_set_ratio) + janela de horário; na dúvida, NÃO casa — um arb
perdido custa menos que um alerta errado.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime

import structlog
from rapidfuzz import fuzz

from beto.matching.normalize import normalize_team
from beto.models import OddsQuote, Outcome, Sport

log = structlog.get_logger(__name__)

_NEAR_MISS_FLOOR = 70


@dataclass(slots=True)
class MatchedEvent:
    """Um jogo com as quotes de todas as casas que o oferecem."""

    key: str
    sport: Sport
    home_team: str
    away_team: str
    league: str | None
    start_time: datetime | None
    quotes: list[OddsQuote] = field(default_factory=list)


@dataclass(slots=True)
class _Cluster:
    sport: Sport
    norm_home: str
    norm_away: str
    display_home: str
    display_away: str
    league: str | None
    start_time: datetime | None
    quotes: list[OddsQuote] = field(default_factory=list)


def _flip_outcomes(outcomes: tuple[Outcome, ...]) -> tuple[Outcome, ...]:
    swap = {"home": "away", "away": "home"}
    return tuple(Outcome(swap.get(o.label, o.label), o.decimal_odds) for o in outcomes)


class EventMatcher:
    def __init__(self, *, fuzzy_threshold: int = 85, time_window_min: int = 30) -> None:
        self.threshold = fuzzy_threshold
        self.window_s = time_window_min * 60

    def _time_compatible(self, a: datetime | None, b: datetime | None) -> bool:
        if a is None or b is None:
            # sem horário dos dois lados não dá para rejeitar — nomes decidem
            return True
        return abs((a - b).total_seconds()) <= self.window_s

    def _names_match(self, ha: str, aa: str, hb: str, ab: str) -> str | None:
        """Devolve "direct", "swapped" ou None. Loga near-misses p/ curar aliases."""
        direct = min(fuzz.token_set_ratio(ha, hb), fuzz.token_set_ratio(aa, ab))
        if direct >= self.threshold:
            return "direct"
        swapped = min(fuzz.token_set_ratio(ha, ab), fuzz.token_set_ratio(aa, hb))
        if swapped >= self.threshold:
            return "swapped"
        if max(direct, swapped) >= _NEAR_MISS_FLOOR:
            log.debug(
                "matcher.near_miss",
                a=f"{ha} x {aa}",
                b=f"{hb} x {ab}",
                ratio=max(direct, swapped),
            )
        return None

    def group(self, quotes: list[OddsQuote]) -> list[MatchedEvent]:
        clusters: list[_Cluster] = []
        for quote in quotes:
            norm_home = normalize_team(quote.home_team)
            norm_away = normalize_team(quote.away_team)
            placed = False
            for cluster in clusters:
                if quote.sport != cluster.sport:
                    continue
                if not self._time_compatible(quote.start_time, cluster.start_time):
                    continue
                how = self._names_match(
                    norm_home, norm_away, cluster.norm_home, cluster.norm_away
                )
                if how is None:
                    continue
                if how == "swapped":
                    quote = replace(
                        quote,
                        home_team=cluster.display_home,
                        away_team=cluster.display_away,
                        outcomes=_flip_outcomes(quote.outcomes),
                    )
                cluster.quotes.append(quote)
                if cluster.start_time is None:
                    cluster.start_time = quote.start_time
                if cluster.league is None:
                    cluster.league = quote.league
                placed = True
                break
            if not placed:
                clusters.append(
                    _Cluster(
                        sport=quote.sport,
                        norm_home=norm_home,
                        norm_away=norm_away,
                        display_home=quote.home_team,
                        display_away=quote.away_team,
                        league=quote.league,
                        start_time=quote.start_time,
                        quotes=[quote],
                    )
                )

        events: list[MatchedEvent] = []
        for c in clusters:
            day = c.start_time.date().isoformat() if c.start_time else "?"
            raw = f"{c.sport.value}|{c.norm_home}|{c.norm_away}|{day}"
            key = hashlib.sha1(raw.encode()).hexdigest()[:16]
            events.append(
                MatchedEvent(
                    key=key,
                    sport=c.sport,
                    home_team=c.display_home,
                    away_team=c.display_away,
                    league=c.league,
                    start_time=c.start_time,
                    quotes=c.quotes,
                )
            )
        return events
