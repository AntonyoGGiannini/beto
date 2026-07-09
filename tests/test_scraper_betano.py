"""Betano: parser dedicado (shape Kaizen) + colheitadeira genérica, ambos offline."""

from __future__ import annotations

import json
from pathlib import Path

from beto.models import MarketType
from beto.scrapers.betano import parse_kaizen
from beto.scrapers.harvest import harvest_payload

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "betano_wc.json").read_text())

EXPECTED_1X2 = {"home": 1.65, "draw": 3.90, "away": 5.50}


def test_shape_betano_e_colhido():
    quotes = harvest_payload(
        FIXTURE, house="betano", include=["copa do mundo"], exclude=[]
    )
    assert {(q.market_type, q.line) for q in quotes} == {
        (MarketType.MATCH_1X2, None),
        (MarketType.OU_GOALS, 2.5),
    }
    q1x2 = next(q for q in quotes if q.market_type is MarketType.MATCH_1X2)
    assert (q1x2.home_team, q1x2.away_team) == ("Argentina", "Chile")
    assert q1x2.league == "Copa do Mundo 2026"
    assert {o.label: o.decimal_odds for o in q1x2.outcomes} == EXPECTED_1X2


def test_parser_dedicado_kaizen():
    """O parser dedicado (independente da heurística) extrai o mesmo shape Kaizen."""
    quotes = parse_kaizen(FIXTURE, house="betano", include=["copa do mundo"], exclude=[])
    assert {(q.market_type, q.line) for q in quotes} == {
        (MarketType.MATCH_1X2, None),
        (MarketType.OU_GOALS, 2.5),
    }
    q1x2 = next(q for q in quotes if q.market_type is MarketType.MATCH_1X2)
    assert (q1x2.home_team, q1x2.away_team) == ("Argentina", "Chile")
    assert q1x2.source_event_id == "ev-arg-chi"
    assert {o.label: o.decimal_odds for o in q1x2.outcomes} == EXPECTED_1X2


def test_parser_dedicado_filtra_competicao():
    assert parse_kaizen(FIXTURE, house="betano", include=["brasileirao"], exclude=[]) == []
