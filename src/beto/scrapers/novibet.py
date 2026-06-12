"""Novibet (novibet.bet.br) — render + colheita (endpoint interno ainda não mapeado).

O site é um SPA JS-pesado; renderizamos a página da Copa e colhemos os JSONs que o
próprio site buscar. Para promover a httpx puro: rode `beto collect --debug-dump`,
ache em debug/novibet/ o XHR com as odds e escreva um parser dedicado.
"""

from __future__ import annotations

from beto.scrapers.render_base import RenderHarvestScraper


class NovibetScraper(RenderHarvestScraper):
    house = "novibet"
    note = "render+colheita (endpoint não mapeado)"
    url_candidates = (
        "https://www.novibet.bet.br/apostas-esportivas/futebol/copa-do-mundo",
        "https://www.novibet.bet.br/apostas-esportivas/futebol",
        "https://www.novibet.bet.br/",
    )
