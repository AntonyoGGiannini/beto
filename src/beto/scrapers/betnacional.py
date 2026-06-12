"""Betnacional (betnacional.bet.br) — render + colheita (endpoint não mapeado)."""

from __future__ import annotations

from beto.scrapers.render_base import RenderHarvestScraper


class BetnacionalScraper(RenderHarvestScraper):
    house = "betnacional"
    note = "render+colheita (endpoint não mapeado)"
    url_candidates = (
        "https://betnacional.bet.br/esportes/futebol",
        "https://betnacional.bet.br/",
        "https://www.betnacional.bet.br/",
    )
