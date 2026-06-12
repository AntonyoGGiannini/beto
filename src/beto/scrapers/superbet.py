"""Superbet (superbet.bet.br) — render + colheita (endpoint interno ainda não mapeado).

A plataforma Superbet costuma expor uma "offer API" JSON limpa (padrão
`offer.superbet.<tld>/v2/<locale>/events/...` em outros países) — vale mapear com
`--debug-dump` e promover este adaptador a httpx puro.
"""

from __future__ import annotations

from beto.scrapers.render_base import RenderHarvestScraper


class SuperbetScraper(RenderHarvestScraper):
    house = "superbet"
    note = "render+colheita (endpoint não mapeado)"
    url_candidates = (
        "https://superbet.bet.br/apostas-esportivas/futebol/copa-do-mundo",
        "https://superbet.bet.br/apostas-esportivas/futebol",
        "https://superbet.bet.br/",
        "https://www.superbet.bet.br/",
    )
