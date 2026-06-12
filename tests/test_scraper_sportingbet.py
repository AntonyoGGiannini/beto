"""Parser dedicado da CDS API (Entain/Sportingbet) contra fixture salva — sem rede."""

from __future__ import annotations

import json
from pathlib import Path

from beto.models import MarketType
from beto.scrapers.sportingbet import parse_cds_fixtures

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "sportingbet_cds_wc.json").read_text()
)


def test_parse_cds_filtra_competicao_e_mapeia_mercados():
    quotes = parse_cds_fixtures(
        FIXTURE, house="sportingbet", include=["copa do mundo"], exclude=[]
    )
    # só o jogo da Copa entra (o do Brasileirão é filtrado)
    assert {q.home_team for q in quotes} == {"Brasil"}
    assert {(q.market_type, q.line) for q in quotes} == {
        (MarketType.MATCH_1X2, None),
        (MarketType.OU_GOALS, 2.5),
        (MarketType.OU_CORNERS, 9.5),
    }

    q1x2 = next(q for q in quotes if q.market_type is MarketType.MATCH_1X2)
    assert q1x2.league == "Copa do Mundo FIFA 2026"
    assert q1x2.start_time is not None and q1x2.start_time.hour == 19
    assert q1x2.source_event_id == "99001"
    assert {o.label: o.decimal_odds for o in q1x2.outcomes} == {
        "home": 2.05,
        "draw": 3.25,
        "away": 3.60,
    }

    goals = next(q for q in quotes if q.market_type is MarketType.OU_GOALS)
    assert {o.label: o.decimal_odds for o in goals.outcomes} == {
        "over": 1.95,
        "under": 1.83,
    }


def test_parse_cds_sem_filtro_traz_tudo():
    quotes = parse_cds_fixtures(FIXTURE, house="sportingbet", include=[], exclude=[])
    assert {q.home_team for q in quotes} == {"Brasil", "Flamengo"}
