"""Relatório de cobertura: precisa dizer claramente quais casas coletaram."""

from __future__ import annotations

from beto.models import Outcome
from beto.report import format_coverage_report
from beto.scrapers.base import ScrapeResult
from helpers import make_quote


def test_relatorio_mostra_status_por_casa():
    results = [
        ScrapeResult(
            "mock",
            [make_quote("mock_alpha", outcomes=[Outcome("home", 2.0)])],
            ok=True,
            duration_s=0.1,
        ),
        ScrapeResult("novibet", [], ok=True, duration_s=2.0),
        ScrapeResult("bet365", [], ok=False, error="HTTP 403 ao carregar", duration_s=1.5),
    ]
    report = format_coverage_report(results, competition_label="Copa do Mundo 2026 — futebol")

    assert "RELATÓRIO DE COBERTURA" in report
    assert "Copa do Mundo 2026" in report
    for house in ("mock", "novibet", "bet365"):
        assert house in report
    assert "OK" in report
    assert "SEM DADOS" in report
    assert "FALHOU" in report
    assert "HTTP 403" in report
    assert "Coletadas: 1/3 — mock" in report
