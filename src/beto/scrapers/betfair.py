"""Betfair (betfair.bet.br, sportsbook regulado) — render + colheita.

Atenção: é o sportsbook brasileiro, não a exchange internacional. Endpoint interno
ainda não mapeado — usar `--debug-dump` para promover a httpx puro.
"""

from __future__ import annotations

from beto.scrapers.render_base import RenderHarvestScraper


class BetfairScraper(RenderHarvestScraper):
    house = "betfair"
    note = "render+colheita (endpoint não mapeado)"
    url_candidates = (
        "https://www.betfair.bet.br/apostas/futebol/copa-do-mundo",
        "https://www.betfair.bet.br/apostas/futebol",
        "https://www.betfair.bet.br/",
    )
