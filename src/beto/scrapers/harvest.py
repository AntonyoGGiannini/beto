"""Colheitadeira genérica ("best-effort") de odds em payloads JSON de sportsbooks.

As casas devolvem JSONs internos com formatos diferentes mas estrutura parecida:
eventos com dois participantes, hora de início e mercados com seleções precificadas.
Este módulo varre qualquer payload de forma **event-centric**: primeiro localiza os
nós que parecem eventos (dois times + hora/mercados), depois colhe os mercados-alvo
(1X2, O/U gols, O/U escanteios) dentro de cada um. É a estratégia padrão dos
adaptadores cujo endpoint interno ainda não foi mapeado à mão — e o fallback dos demais.

Além das quotes, devolve um `HarvestReport` com o funil (eventos → em-escopo →
mercados → quotes) que alimenta o diagnóstico de "SEM DADOS".

Filosofia: conservador. Mercado ambíguo (1º tempo, handicap, time da casa, etc.) é
descartado; um arb perdido custa menos que um alerta errado.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from rapidfuzz import fuzz

from beto.models import MarketType, OddsQuote, Outcome, Sport
from beto.scrapers.common import (
    is_drawish,
    matches_competition,
    norm_text,
    ou_label,
    parse_line,
    parse_start_time,
    to_odds,
)

# chaves comumente usadas pelos sites (ordem importa: mais específicas primeiro)
NAME_KEYS = (
    "name",
    "label",
    "title",
    "marketName",
    "market_name",
    "displayName",
    "description",
    "caption",
    "text",
    "value",
)
SELECTION_LIST_KEYS = (
    "selections",
    "outcomes",
    "options",
    "results",
    "odds",
    "choices",
    "runners",
    "prices",
)
PRICE_KEYS = (
    "decimalOdds",
    "priceDecimal",
    "decimal",
    "odds",
    "odd",
    "price",
    "rate",
    "coeff",
    "value",
    "k",
    "o",
)
TEAM_LIST_KEYS = ("participants", "competitors", "teams", "opponents", "runners")
HOME_KEYS = ("homeTeam", "home_team", "homeName", "localTeam", "team1", "home")
AWAY_KEYS = ("awayTeam", "away_team", "awayName", "visitorTeam", "team2", "away")
START_KEYS = (
    "startTime",
    "start_time",
    "startDate",
    "start_date",
    "kickoff",
    "kickOff",
    "eventDate",
    "utcDate",
    "startTimestamp",
    "scheduledStart",
    "start",
    "date",
)
LEAGUE_KEYS = (
    "competitionName",
    "leagueName",
    "tournamentName",
    "league",
    "competition",
    "tournament",
    "championship",
    "category",
    "group",
)
ID_KEYS = ("eventId", "fixtureId", "matchId", "id")
_SEPARATORS = (" - ", " x ", " vs. ", " vs ", " v ", " — ", " @ ")
MARKET_CONTAINER_KEYS = ("markets", "optionMarkets", "games", "marketGroups", "bettingMarkets")

MARKET_KEYWORDS: dict[MarketType, tuple[str, ...]] = {
    MarketType.OU_CORNERS: (
        "total de escanteios",
        "escanteios mais/menos",
        "escanteios",
        "total corners",
        "corners over/under",
        "corners",
        "cantos",
    ),
    MarketType.OU_GOALS: (
        "total de gols",
        "total de golos",
        "gols mais/menos",
        "mais/menos gols",
        "total goals",
        "goals over/under",
        "over/under",
        "mais/menos",
        "total do jogo",
    ),
    MarketType.MATCH_1X2: (
        "resultado final",
        "resultado da partida",
        "vencedor do encontro",
        "vencedor da partida",
        "1x2",
        "match result",
        "full time result",
        "match winner",
        "resultado",
    ),
}

# mercados que NÃO queremos confundir com os acima
REJECT_WORDS = (
    "tempo",  # pega "1º tempo" / "segundo tempo"
    "intervalo",
    "half",
    "ht ",
    "asiatic",
    "asian",
    "empate anula",
    "dupla",
    "double chance",
    "handicap",
    "jogador",
    "player",
    "cartao",
    "cartoes",
    "card",
    "escanteio do",
    "da casa",
    "do visitante",
    "casa)",
    "fora)",
    "exato",
    "exact",
    "minuto",
    "primeiro",
    "ultimo",
)


def classify_market(name: str) -> MarketType | None:
    n = norm_text(name)
    if any(w in n for w in REJECT_WORDS):
        return None
    for mtype, keywords in MARKET_KEYWORDS.items():
        if any(k in n for k in keywords):
            return mtype
    return None


@dataclass(slots=True)
class RawSelection:
    name: str
    odds: float


@dataclass(slots=True)
class EventCtx:
    home: str
    away: str
    start: datetime | None = None
    league: str | None = None
    source_id: str | None = None

    def key(self) -> tuple[str, str, str]:
        day = self.start.date().isoformat() if self.start else "?"
        return (norm_text(self.home), norm_text(self.away), day)


@dataclass(slots=True)
class HarvestReport:
    """Quotes + funil da colheita (para diagnóstico do 'SEM DADOS')."""

    quotes: list[OddsQuote] = field(default_factory=list)
    raw_events: int = 0   # eventos candidatos distintos
    in_scope: int = 0     # eventos que passaram no filtro de competição
    markets: int = 0      # mercados-alvo reconhecidos


def _unwrap(v: Any) -> Any:
    """Plataformas como a Entain embrulham strings em {"value": "..."}."""
    if isinstance(v, dict) and "value" in v and isinstance(v["value"], str | int | float):
        return v["value"]
    return v


def _get_name(d: dict[str, Any]) -> str | None:
    for k in NAME_KEYS:
        v = _unwrap(d.get(k))
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _get_price(d: dict[str, Any]) -> float | None:
    for k in PRICE_KEYS:
        v = d.get(k)
        if isinstance(v, dict):
            for kk in PRICE_KEYS:
                if kk in v:
                    odds = to_odds(v[kk])
                    if odds is not None:
                        return odds
        else:
            odds = to_odds(v)
            if odds is not None:
                return odds
    return None


def _extract_selections(d: dict[str, Any]) -> list[RawSelection] | None:
    for k in SELECTION_LIST_KEYS:
        v = d.get(k)
        if not (isinstance(v, list) and v and all(isinstance(i, dict) for i in v)):
            continue
        sels: list[RawSelection] = []
        for item in v:
            name = _get_name(item)
            price = _get_price(item)
            if name is None or price is None:
                sels = []
                break
            sels.append(RawSelection(name, price))
        if len(sels) >= 2:
            return sels
    return None


def _name_of(v: Any) -> str | None:
    v = _unwrap(v)
    if isinstance(v, str) and v.strip():
        return v.strip()
    if isinstance(v, dict):
        return _get_name(v)
    return None


def _looks_like_event(node: dict[str, Any]) -> bool:
    return any(k in node for k in START_KEYS) or any(k in node for k in MARKET_CONTAINER_KEYS)


def _teams_from(node: dict[str, Any]) -> tuple[str, str] | None:
    for k in TEAM_LIST_KEYS:
        v = node.get(k)
        if isinstance(v, list) and len(v) >= 2:
            a, b = _name_of(v[0]), _name_of(v[1])
            if a and b and a != b:
                return a, b
    for hk in HOME_KEYS:
        if hk in node:
            for ak in AWAY_KEYS:
                if ak in node:
                    a, b = _name_of(node[hk]), _name_of(node[ak])
                    if a and b and a != b:
                        return a, b
    # último recurso: "Brasil - México" no nome do evento
    name = _get_name(node)
    if name:
        for sep in _SEPARATORS:
            if sep in name:
                a, b = (p.strip() for p in name.split(sep, 1))
                if len(a) > 1 and len(b) > 1 and norm_text(a) != norm_text(b):
                    return a, b
    return None


def _resolve_league(node: dict[str, Any], ancestors: list[dict[str, Any]]) -> str | None:
    for source in (node, *reversed(ancestors)):
        for k in LEAGUE_KEYS:
            name = _name_of(source.get(k))
            if name:
                return name
    return None


def _resolve_start(node: dict[str, Any]) -> datetime | None:
    for k in START_KEYS:
        if k in node:
            dt = parse_start_time(_unwrap(node[k]))
            if dt:
                return dt
    return None


def _resolve_source_id(node: dict[str, Any]) -> str | None:
    for k in ID_KEYS:
        v = _unwrap(node.get(k))
        if isinstance(v, str | int):
            return str(v)
    return None


def _iter_events(
    payload: Any, assume_competition: str | None
) -> Iterator[tuple[dict[str, Any], EventCtx]]:
    """Percorre o payload e emite (nó do evento, contexto) para cada evento distinto.

    Ao reconhecer um evento (dois times + cara de evento) NÃO desce mais em busca de
    outros eventos ali dentro — mas os mercados desse evento são varridos por
    `_iter_markets`. Isso dá contagens limpas e evita eventos aninhados fantasmas.
    """

    def walk(node: Any, ancestors: list[dict[str, Any]]) -> Iterator[tuple[dict, EventCtx]]:
        if isinstance(node, dict):
            teams = _teams_from(node)
            if teams and _looks_like_event(node):
                home, away = teams
                yield node, EventCtx(
                    home=home,
                    away=away,
                    start=_resolve_start(node),
                    league=_resolve_league(node, ancestors) or assume_competition,
                    source_id=_resolve_source_id(node),
                )
                return  # não desce: já é um evento
            ancestors.append(node)
            for v in node.values():
                yield from walk(v, ancestors)
            ancestors.pop()
        elif isinstance(node, list):
            for item in node:
                yield from walk(item, ancestors)

    yield from walk(payload, [])


def _iter_markets(node: Any) -> Iterator[tuple[str, MarketType, list[RawSelection]]]:
    """Dentro do subárvore de um evento, emite (nome, tipo, seleções) dos mercados-alvo."""
    seen_names: set[str] = set()

    def walk(n: Any) -> Iterator[tuple[str, MarketType, list[RawSelection]]]:
        if isinstance(n, dict):
            name = _get_name(n)
            if name:
                mtype = classify_market(name)
                if mtype is not None:
                    sels = _extract_selections(n)
                    if sels:
                        marker = f"{mtype}:{norm_text(name)}"
                        if marker not in seen_names:
                            seen_names.add(marker)
                            yield name, mtype, sels
            for v in n.values():
                yield from walk(v)
        elif isinstance(n, list):
            for item in n:
                yield from walk(item)

    yield from walk(node)


def _similar(a: str, b: str) -> bool:
    return fuzz.partial_ratio(norm_text(a), norm_text(b)) >= 80


def map_market(
    mtype: MarketType,
    sels: list[RawSelection],
    market_name: str,
    home: str | None,
    away: str | None,
) -> list[tuple[float | None, tuple[Outcome, ...]]]:
    """Mapeia seleções cruas para rótulos canônicos.

    Devolve [(linha, outcomes)] — O/U pode render várias linhas num mercado só.
    Lista vazia = mercado ambíguo, descartado.
    """
    if mtype is MarketType.MATCH_1X2:
        if len(sels) != 3:
            return []
        names = [s.name for s in sels]
        # caso comum: ordem posicional [casa, empate, fora]
        if is_drawish(names[1]):
            outs = (
                Outcome("home", sels[0].odds),
                Outcome("draw", sels[1].odds),
                Outcome("away", sels[2].odds),
            )
            return [(None, outs)]
        # senão, por nome: "1"/"2", nome dos times
        mapped: dict[str, float] = {}
        for s in sels:
            n = norm_text(s.name)
            if is_drawish(s.name):
                mapped["draw"] = s.odds
            elif n == "1" or (home and _similar(s.name, home)):
                mapped["home"] = s.odds
            elif n == "2" or (away and _similar(s.name, away)):
                mapped["away"] = s.odds
        if set(mapped) == {"home", "draw", "away"}:
            return [(None, tuple(Outcome(lb, od) for lb, od in mapped.items()))]
        return []

    # O/U: agrupa por linha (mercados costumam listar várias linhas juntas)
    market_line = parse_line(market_name)
    groups: dict[float, dict[str, float]] = {}
    for s in sels:
        label = ou_label(s.name)
        if label is None:
            continue
        line = parse_line(s.name)
        if line is None:
            line = market_line
        if line is None:
            continue
        groups.setdefault(line, {}).setdefault(label, s.odds)
    out: list[tuple[float | None, tuple[Outcome, ...]]] = []
    for line, g in sorted(groups.items()):
        if set(g) == {"over", "under"}:
            out.append((line, (Outcome("over", g["over"]), Outcome("under", g["under"]))))
    return out


def harvest_report(
    payloads: list[Any],
    *,
    house: str,
    include: list[str],
    exclude: list[str],
    assume_competition: str | None = None,
    url: str | None = None,
    scraped_at: datetime | None = None,
) -> HarvestReport:
    """Varre payloads JSON e devolve quotes + funil de diagnóstico, deduplicando."""
    scraped_at = scraped_at or datetime.now(UTC)
    report = HarvestReport()
    seen_events: set[tuple[str, str, str]] = set()
    seen_scope: set[tuple[str, str, str]] = set()
    seen_quotes: set[tuple[Any, ...]] = set()

    for payload in payloads:
        for node, ctx in _iter_events(payload, assume_competition):
            ekey = ctx.key()
            if ekey not in seen_events:
                seen_events.add(ekey)
                report.raw_events += 1
            if not matches_competition(ctx.league, include, exclude):
                continue
            if ekey not in seen_scope:
                seen_scope.add(ekey)
                report.in_scope += 1
            for mname, mtype, sels in _iter_markets(node):
                mapped = map_market(mtype, sels, mname, ctx.home, ctx.away)
                if mapped:
                    report.markets += 1
                for line, outs in mapped:
                    qkey = (*ekey, mtype, line)
                    if qkey in seen_quotes:
                        continue
                    seen_quotes.add(qkey)
                    report.quotes.append(
                        OddsQuote(
                            house=house,
                            sport=Sport.FOOTBALL,
                            league=ctx.league,
                            home_team=ctx.home,
                            away_team=ctx.away,
                            start_time=ctx.start,
                            market_type=mtype,
                            line=line,
                            outcomes=outs,
                            scraped_at=scraped_at,
                            source_event_id=ctx.source_id,
                            url=url,
                        )
                    )
    return report


def harvest_payload(
    payload: Any,
    *,
    house: str,
    include: list[str],
    exclude: list[str],
    assume_competition: str | None = None,
    url: str | None = None,
    scraped_at: datetime | None = None,
) -> list[OddsQuote]:
    """Compat: colhe um único payload e devolve só as quotes."""
    return harvest_report(
        [payload],
        house=house,
        include=include,
        exclude=exclude,
        assume_competition=assume_competition,
        url=url,
        scraped_at=scraped_at,
    ).quotes


def harvest_many(
    payloads: list[Any],
    *,
    house: str,
    include: list[str],
    exclude: list[str],
    assume_competition: str | None = None,
    url: str | None = None,
) -> list[OddsQuote]:
    """Compat: colhe vários payloads e devolve só as quotes (deduplicadas)."""
    return harvest_report(
        payloads,
        house=house,
        include=include,
        exclude=exclude,
        assume_competition=assume_competition,
        url=url,
    ).quotes


_SCRIPT_RX = re.compile(r"<script[^>]*>(.*?)</script>", re.S | re.I)
_ASSIGN_RX = re.compile(r"^\s*(?:window\.[\w$.\[\]'\"]+|var\s+\w+|let\s+\w+|const\s+\w+)\s*=\s*")


def extract_embedded_json(html: str) -> list[Any]:
    """Extrai JSONs embutidos em <script> (__NEXT_DATA__, window.__STATE__ = {...}, etc.)."""
    out: list[Any] = []
    for m in _SCRIPT_RX.finditer(html):
        body = _ASSIGN_RX.sub("", m.group(1).strip()).strip().rstrip(";").strip()
        if not body or body[0] not in "[{":
            continue
        try:
            out.append(json.loads(body))
        except (ValueError, RecursionError):
            continue
    return out
