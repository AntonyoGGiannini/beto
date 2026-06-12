"""Testes do casamento de eventos entre casas."""

from __future__ import annotations

from datetime import timedelta

import pytest

from beto.arbitrage.engine import find_arbitrage
from beto.matching.matcher import EventMatcher
from beto.models import MarketType, Outcome
from helpers import KICKOFF, make_quote


def test_alias_ingles_portugues_casa_no_mesmo_evento():
    quotes = [
        make_quote("casa_a", home="Brasil", away="México",
                   outcomes=[Outcome("home", 2.0), Outcome("draw", 3.0), Outcome("away", 4.0)]),
        make_quote("casa_b", home="Brazil", away="Mexico",
                   outcomes=[Outcome("home", 2.1), Outcome("draw", 3.1), Outcome("away", 4.1)]),
    ]
    events = EventMatcher().group(quotes)
    assert len(events) == 1
    assert len(events[0].quotes) == 2


def test_swap_casa_fora_inverte_rotulos():
    """Casa B lista "México x Brasil": as odds home/away dela são invertidas ao casar."""
    quotes = [
        make_quote("casa_a", home="Brasil", away="México",
                   outcomes=[Outcome("home", 2.10), Outcome("draw", 3.10), Outcome("away", 3.50)]),
        make_quote("casa_b", home="México", away="Brasil",
                   outcomes=[Outcome("home", 4.20), Outcome("draw", 3.60), Outcome("away", 1.95)]),
    ]
    events = EventMatcher().group(quotes)
    assert len(events) == 1
    event = events[0]
    assert event.home_team == "Brasil"

    flipped = next(q for q in event.quotes if q.house == "casa_b")
    odds = {o.label: o.decimal_odds for o in flipped.outcomes}
    assert odds == {"away": 4.20, "draw": 3.60, "home": 1.95}

    # com o flip, vira exatamente o exemplo trabalhado: lucro 0,80%
    opp = find_arbitrage(event, MarketType.MATCH_1X2, None, bankroll=1000.0)
    assert opp is not None
    assert opp.profit_pct == pytest.approx(0.80, abs=1e-9)


def test_horarios_distantes_nao_casam():
    quotes = [
        make_quote("casa_a", outcomes=[Outcome("home", 2.0)]),
        make_quote("casa_b", start=KICKOFF + timedelta(hours=3),
                   outcomes=[Outcome("home", 2.1)]),
    ]
    events = EventMatcher(time_window_min=30).group(quotes)
    assert len(events) == 2


def test_horario_dentro_da_janela_casa():
    quotes = [
        make_quote("casa_a", outcomes=[Outcome("home", 2.0)]),
        make_quote("casa_b", start=KICKOFF + timedelta(minutes=10),
                   outcomes=[Outcome("home", 2.1)]),
    ]
    events = EventMatcher(time_window_min=30).group(quotes)
    assert len(events) == 1


def test_times_diferentes_nao_casam():
    quotes = [
        make_quote("casa_a", home="Brasil", away="México", outcomes=[Outcome("home", 2.0)]),
        make_quote("casa_b", home="Argentina", away="Chile", outcomes=[Outcome("home", 2.1)]),
    ]
    events = EventMatcher().group(quotes)
    assert len(events) == 2


def test_variacoes_de_clube_casam():
    quotes = [
        make_quote("casa_a", home="São Paulo FC", away="Palmeiras", league="Brasileirão",
                   outcomes=[Outcome("home", 2.0)]),
        make_quote("casa_b", home="SAO PAULO", away="Palmeiras", league="Brasileirão",
                   outcomes=[Outcome("home", 2.1)]),
    ]
    events = EventMatcher().group(quotes)
    assert len(events) == 1
