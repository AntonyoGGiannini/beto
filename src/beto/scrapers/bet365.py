"""bet365 (bet365.bet.br) — A CASA MAIS DIFÍCIL; falha esperada sem medidas extras.

A bet365 usa proteção anti-bot pesada (fingerprint TLS, desafios JS, análise
comportamental) e entrega odds por um protocolo próprio (WebSocket/delta), não por
XHR JSON simples. Playwright "puro" costuma ser bloqueado em segundos.

Este adaptador tenta o caminho render+colheita mesmo assim (custo zero), mas o
suporte real provavelmente exigirá proxy residencial + browser stealth — fora do
escopo do MVP (ver README). Mantida no registry para o relatório de cobertura ser
honesto sobre ela.
"""

from __future__ import annotations

from beto.scrapers.render_base import RenderHarvestScraper


class Bet365Scraper(RenderHarvestScraper):
    house = "bet365"
    note = "anti-bot pesado — falha esperada sem proxy/stealth"
    url_candidates = (
        "https://www.bet365.bet.br/",
    )
