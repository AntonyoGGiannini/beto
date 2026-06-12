"""Testes da normalização de nomes de times/seleções."""

from __future__ import annotations

from beto.matching.normalize import normalize_team


def test_acentos_e_caixa():
    assert normalize_team("São Paulo FC") == "sao paulo"
    assert normalize_team("SAO PAULO") == "sao paulo"


def test_alias_ingles_para_portugues():
    assert normalize_team("Brazil") == "brasil"
    assert normalize_team("Germany") == "alemanha"
    assert normalize_team("United States") == "estados unidos"
    assert normalize_team("Côte d'Ivoire") == "costa do marfim"


def test_tokens_de_ruido_removidos():
    assert normalize_team("Palmeiras SE") == "palmeiras"
    assert normalize_team("Clube Atlético Mineiro") == "atletico mineiro"


def test_identidade_simples():
    assert normalize_team("Argentina") == "argentina"
