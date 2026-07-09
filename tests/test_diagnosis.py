"""Diagnóstico do 'SEM DADOS': funil da colheita + dica acionável por estágio.

Este é o coração da reescrita — transformar '0 eventos' silencioso em uma causa
provável (bloqueio? shape novo? filtro de competição? mercado não reconhecido?).
"""

from __future__ import annotations

from beto.scrapers.base import ScrapeDiag
from beto.scrapers.harvest import harvest_report

INCLUDE = ["copa do mundo"]


def _event(name: str, league: str) -> dict:
    return {
        "name": name,
        "startTime": 1781467200000,
        "league": {"name": league},
        "markets": [
            {
                "name": "Resultado Final",
                "selections": [
                    {"name": "A", "price": 2.10},
                    {"name": "Empate", "price": 3.10},
                    {"name": "B", "price": 3.50},
                ],
            }
        ],
    }


def test_funil_conta_eventos_escopo_e_mercados():
    payload = {
        "events": [
            _event("Brasil - México", "Copa do Mundo 2026"),
            _event("Flamengo - Palmeiras", "Brasileirão Série A"),  # fora de escopo
        ]
    }
    report = harvest_report(payload_list := [payload], house="t", include=INCLUDE, exclude=[])
    assert len(payload_list) == 1
    assert report.raw_events == 2          # dois eventos reconhecidos
    assert report.in_scope == 1            # só o da Copa passa no filtro
    assert report.markets == 1             # um mercado-alvo mapeado
    assert len(report.quotes) == 1


def test_hint_filtro_de_competicao_removeu_tudo():
    payload = {"events": [_event("Flamengo - Palmeiras", "Brasileirão Série A")]}
    report = harvest_report([payload], house="t", include=INCLUDE, exclude=[])
    assert report.quotes == []
    diag = ScrapeDiag(payloads=1, raw_events=report.raw_events, in_scope=report.in_scope)
    hint = diag.bottleneck_hint(includes=INCLUDE)
    assert hint is not None and "0 na competição" in hint


def _hint(**kwargs) -> str | None:
    return ScrapeDiag(**kwargs).bottleneck_hint(includes=INCLUDE)


def test_hint_por_estagio():
    assert "HTTP 403" in _hint(http_status=403)               # bloqueio HTTP
    assert "sem JSON" in _hint(payloads=0)                    # sem JSON reconhecível
    assert "nenhum evento" in _hint(payloads=1, raw_events=0)  # payload sem eventos
    # eventos em escopo mas nenhum mercado-alvo
    assert "nenhum mercado-alvo" in _hint(payloads=1, raw_events=3, in_scope=2, markets=0)
    # mercados achados mas sem odds completas
    assert "sem odds completas" in _hint(payloads=1, raw_events=3, in_scope=2, markets=1)
    assert _hint(quotes=2) is None                            # rendeu quotes → sem dica
