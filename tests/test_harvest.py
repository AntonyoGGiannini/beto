"""Testes da colheitadeira heurística de payloads JSON."""

from __future__ import annotations

from beto.models import MarketType
from beto.scrapers.harvest import extract_embedded_json, harvest_payload

INCLUDE = ["copa do mundo"]
EXCLUDE = ["feminin", "sub-"]

GENERIC_PAYLOAD = {
    "data": {
        "events": [
            {
                "id": 123,
                "name": "Brasil - México",
                "startTime": 1781377200000,  # epoch em ms (jun/2026)
                "league": {"name": "Copa do Mundo 2026"},
                "markets": [
                    {
                        "name": "Resultado Final",
                        "selections": [
                            {"name": "Brasil", "price": 2.10},
                            {"name": "Empate", "price": 3.10},
                            {"name": "México", "price": 3.50},
                        ],
                    },
                    {
                        "name": "Total de Gols",
                        "selections": [
                            {"name": "Mais de 2.5", "price": 1.90},
                            {"name": "Menos de 2.5", "price": 1.85},
                            {"name": "Mais de 3.5", "price": 2.80},
                            {"name": "Menos de 3.5", "price": 1.40},
                        ],
                    },
                    {
                        "name": "Total de Escanteios",
                        "selections": [
                            {"name": "Mais de 9.5", "price": 1.80},
                            {"name": "Menos de 9.5", "price": 1.90},
                        ],
                    },
                    {
                        # deve ser rejeitado: mercado de 1º tempo
                        "name": "Resultado Final - 1º Tempo",
                        "selections": [
                            {"name": "Brasil", "price": 2.80},
                            {"name": "Empate", "price": 2.40},
                            {"name": "México", "price": 4.50},
                        ],
                    },
                ],
            }
        ]
    }
}


def test_payload_generico_extrai_tres_mercados():
    quotes = harvest_payload(
        GENERIC_PAYLOAD, house="teste", include=INCLUDE, exclude=EXCLUDE
    )
    by_market = {(q.market_type, q.line) for q in quotes}
    assert by_market == {
        (MarketType.MATCH_1X2, None),
        (MarketType.OU_GOALS, 2.5),
        (MarketType.OU_GOALS, 3.5),
        (MarketType.OU_CORNERS, 9.5),
    }
    quote_1x2 = next(q for q in quotes if q.market_type is MarketType.MATCH_1X2)
    assert quote_1x2.home_team == "Brasil"
    assert quote_1x2.away_team == "México"
    assert quote_1x2.start_time is not None and quote_1x2.start_time.year == 2026
    odds = {o.label: o.decimal_odds for o in quote_1x2.outcomes}
    assert odds == {"home": 2.10, "draw": 3.10, "away": 3.50}

    ou_25 = next(q for q in quotes if q.line == 2.5)
    assert {o.label: o.decimal_odds for o in ou_25.outcomes} == {"over": 1.90, "under": 1.85}


def test_competicao_excluida_e_filtrada():
    payload = {
        "events": [
            {
                "name": "Brasil - México",
                "startTime": 1781377200000,
                "league": {"name": "Copa do Mundo Feminina"},
                "markets": GENERIC_PAYLOAD["data"]["events"][0]["markets"][:1],
            }
        ]
    }
    assert harvest_payload(payload, house="t", include=INCLUDE, exclude=EXCLUDE) == []


def test_sem_liga_usa_assume_competition():
    payload = {
        "events": [
            {
                "name": "Argentina - Chile",
                "startTime": 1781377200000,
                "markets": [
                    {
                        "name": "Resultado Final",
                        "selections": [
                            {"name": "1", "price": 1.80},
                            {"name": "X", "price": 3.50},
                            {"name": "2", "price": 4.60},
                        ],
                    }
                ],
            }
        ]
    }
    # sem assume → filtro de competição derruba (liga desconhecida)
    assert harvest_payload(payload, house="t", include=INCLUDE, exclude=EXCLUDE) == []
    # com assume (página já era da Copa) → entra
    quotes = harvest_payload(
        payload,
        house="t",
        include=INCLUDE,
        exclude=EXCLUDE,
        assume_competition="Copa do Mundo 2026",
    )
    assert len(quotes) == 1
    assert quotes[0].league == "Copa do Mundo 2026"


def test_1x2_mapeado_por_nome_quando_fora_de_ordem():
    payload = {
        "events": [
            {
                "name": "Brasil - México",
                "startTime": 1781377200000,
                "league": {"name": "Copa do Mundo 2026"},
                "markets": [
                    {
                        "name": "Resultado Final",
                        "selections": [
                            {"name": "Brasil", "price": 2.10},
                            {"name": "México", "price": 3.50},
                            {"name": "Empate", "price": 3.10},
                        ],
                    }
                ],
            }
        ]
    }
    quotes = harvest_payload(payload, house="t", include=INCLUDE, exclude=EXCLUDE)
    assert len(quotes) == 1
    odds = {o.label: o.decimal_odds for o in quotes[0].outcomes}
    assert odds == {"home": 2.10, "away": 3.50, "draw": 3.10}


def test_extract_embedded_json():
    html = """
    <html><head>
    <script id="__NEXT_DATA__" type="application/json">{"a": 1}</script>
    <script>window.__INITIAL_STATE__ = {"b": [1, 2]};</script>
    <script>console.log('nada');</script>
    </head></html>
    """
    payloads = extract_embedded_json(html)
    assert {"a": 1} in payloads
    assert {"b": [1, 2]} in payloads
    assert len(payloads) == 2
