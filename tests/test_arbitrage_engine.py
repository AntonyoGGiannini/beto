"""Testes do motor de arbitragem — exemplos numéricos trabalhados à mão."""

from __future__ import annotations

import pytest

from beto.arbitrage.engine import find_all, find_arbitrage
from beto.matching.matcher import EventMatcher
from beto.models import MarketType, Outcome
from helpers import make_quote


def _single_event(quotes):
    events = EventMatcher().group(list(quotes))
    assert len(events) == 1
    return events[0]


def test_three_way_surebet_worked_example():
    """1X2 com melhores odds 2.10/3.60/4.20 → arb_sum 125/126, lucro exato 0,80%."""
    event = _single_event(
        [
            make_quote(
                "casa_a",
                outcomes=[Outcome("home", 2.10), Outcome("draw", 3.10), Outcome("away", 3.50)],
            ),
            make_quote(
                "casa_b",
                outcomes=[Outcome("home", 1.95), Outcome("draw", 3.60), Outcome("away", 4.20)],
            ),
        ]
    )
    opp = find_arbitrage(event, MarketType.MATCH_1X2, None, bankroll=1000.0)
    assert opp is not None
    assert opp.arb_sum == pytest.approx(1 / 2.10 + 1 / 3.60 + 1 / 4.20)
    assert opp.profit_pct == pytest.approx(0.80, abs=1e-9)

    legs = {leg.outcome_label: leg for leg in opp.legs}
    assert legs["home"].house == "casa_a" and legs["home"].odds == 2.10
    assert legs["draw"].house == "casa_b" and legs["draw"].odds == 3.60
    assert legs["away"].house == "casa_b" and legs["away"].odds == 4.20

    # divisão ótima de stakes: 480/280/240 e retorno igual em toda perna (=1008)
    assert legs["home"].stake == pytest.approx(480.00, abs=0.01)
    assert legs["draw"].stake == pytest.approx(280.00, abs=0.01)
    assert legs["away"].stake == pytest.approx(240.00, abs=0.01)
    guaranteed = 1000.0 / opp.arb_sum
    assert guaranteed == pytest.approx(1008.0, abs=1e-6)
    for leg in opp.legs:
        assert leg.stake * leg.odds == pytest.approx(guaranteed, abs=0.05)
    assert sum(leg.stake_pct for leg in opp.legs) == pytest.approx(1.0)


def test_overround_normal_nao_e_arbitragem():
    event = _single_event(
        [
            make_quote(
                "casa_a",
                outcomes=[Outcome("home", 1.90), Outcome("draw", 3.40), Outcome("away", 4.00)],
            ),
            make_quote(
                "casa_b",
                outcomes=[Outcome("home", 1.85), Outcome("draw", 3.30), Outcome("away", 4.10)],
            ),
        ]
    )
    assert find_arbitrage(event, MarketType.MATCH_1X2, None, bankroll=1000.0) is None


def test_two_way_over_under():
    """O/U 2.5 com 2.05/2.05 → arb_sum 0.9756, lucro 2,5%, stakes 500/500."""
    event = _single_event(
        [
            make_quote(
                "casa_a",
                market=MarketType.OU_GOALS,
                line=2.5,
                outcomes=[Outcome("over", 2.05), Outcome("under", 1.80)],
            ),
            make_quote(
                "casa_b",
                market=MarketType.OU_GOALS,
                line=2.5,
                outcomes=[Outcome("over", 1.80), Outcome("under", 2.05)],
            ),
        ]
    )
    opp = find_arbitrage(event, MarketType.OU_GOALS, 2.5, bankroll=1000.0)
    assert opp is not None
    assert opp.profit_pct == pytest.approx(2.5, abs=1e-9)
    for leg in opp.legs:
        assert leg.stake == pytest.approx(500.0, abs=0.01)


def test_linhas_diferentes_nao_se_misturam():
    """over 2.5 numa casa + under 3.5 noutra NÃO é arbitragem do mesmo mercado."""
    event = _single_event(
        [
            make_quote(
                "casa_a",
                market=MarketType.OU_GOALS,
                line=2.5,
                outcomes=[Outcome("over", 2.50), Outcome("under", 1.55)],
            ),
            make_quote(
                "casa_b",
                market=MarketType.OU_GOALS,
                line=3.5,
                outcomes=[Outcome("over", 1.55), Outcome("under", 2.50)],
            ),
        ]
    )
    assert find_arbitrage(event, MarketType.OU_GOALS, 2.5, bankroll=1000.0) is None
    assert find_arbitrage(event, MarketType.OU_GOALS, 3.5, bankroll=1000.0) is None


def test_threshold_filtra_lucro_baixo():
    event = _single_event(
        [
            make_quote(
                "casa_a",
                outcomes=[Outcome("home", 2.10), Outcome("draw", 3.10), Outcome("away", 3.50)],
            ),
            make_quote(
                "casa_b",
                outcomes=[Outcome("home", 1.95), Outcome("draw", 3.60), Outcome("away", 4.20)],
            ),
        ]
    )
    assert (
        find_arbitrage(event, MarketType.MATCH_1X2, None, bankroll=1000.0, min_profit_pct=1.0)
        is None
    )


def test_arb_dentro_de_uma_casa_so_e_rejeitada():
    """Todas as melhores odds na mesma casa = quase certamente bug de parsing."""
    event = _single_event(
        [
            make_quote(
                "casa_a",
                outcomes=[Outcome("home", 2.10), Outcome("draw", 3.60), Outcome("away", 4.20)],
            ),
            make_quote(
                "casa_b",
                outcomes=[Outcome("home", 1.50), Outcome("draw", 3.00), Outcome("away", 3.00)],
            ),
        ]
    )
    assert find_arbitrage(event, MarketType.MATCH_1X2, None, bankroll=1000.0) is None


def test_resultado_sem_preco_em_casa_alguma():
    """1X2 sem o empate precificado em lugar nenhum → sem arbitragem."""
    event = _single_event(
        [
            make_quote("casa_a", outcomes=[Outcome("home", 2.10), Outcome("away", 3.50)]),
            make_quote("casa_b", outcomes=[Outcome("home", 1.95), Outcome("away", 4.20)]),
        ]
    )
    assert find_arbitrage(event, MarketType.MATCH_1X2, None, bankroll=1000.0) is None


def test_find_all_varre_mercados_e_ordena():
    quotes = [
        make_quote(
            "casa_a",
            outcomes=[Outcome("home", 2.10), Outcome("draw", 3.10), Outcome("away", 3.50)],
        ),
        make_quote(
            "casa_b",
            outcomes=[Outcome("home", 1.95), Outcome("draw", 3.60), Outcome("away", 4.20)],
        ),
        make_quote(
            "casa_a",
            market=MarketType.OU_GOALS,
            line=2.5,
            outcomes=[Outcome("over", 1.90), Outcome("under", 1.85)],
        ),
        make_quote(
            "casa_b",
            market=MarketType.OU_GOALS,
            line=2.5,
            outcomes=[Outcome("over", 1.88), Outcome("under", 1.92)],
        ),
    ]
    events = EventMatcher().group(quotes)
    opportunities = find_all(events, bankroll=1000.0, min_profit_pct=0.0)
    assert len(opportunities) == 1  # só o 1X2 fecha conta; o O/U é overround
    assert opportunities[0].market_type is MarketType.MATCH_1X2
