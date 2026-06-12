"""Estratégia genérica para casas sem endpoint JSON mapeado: renderizar e colher.

Abre a página da competição num Chromium headless, captura todos os JSONs que o
site buscou (XHR) + os embutidos no HTML, e passa tudo pela colheitadeira
heurística (`beto.scrapers.harvest`). Tenta cada URL candidata em ordem e para na
primeira que render quotes.

Para mapear o endpoint definitivo de uma casa: rode `beto collect --debug-dump`,
inspecione os payloads salvos em debug/<casa>/ e promova o adaptador para um
parser dedicado (exemplos: betano.py, sportingbet.py).
"""

from __future__ import annotations

import json

import structlog

from beto.models import OddsQuote
from beto.scrapers.base import BookmakerScraper
from beto.scrapers.harvest import extract_embedded_json, harvest_many

log = structlog.get_logger(__name__)


class RenderHarvestScraper(BookmakerScraper):
    # URLs candidatas, da mais específica (página da Copa) para a mais genérica
    # (home — durante o torneio a home destaca os jogos da Copa).
    url_candidates: tuple[str, ...] = ()

    async def scrape(self) -> list[OddsQuote]:
        settings = self.transport.settings
        errors: list[str] = []
        loaded_but_empty = 0
        for url in self.url_candidates:
            try:
                rendered = await self.transport.render_capture(url, tag=self.house)
            except Exception as exc:  # noqa: BLE001 — tenta a próxima candidata
                errors.append(f"{url} → {type(exc).__name__}: {exc}")
                continue
            payloads: list[object] = []
            for captured in rendered.captured:
                try:
                    payloads.append(json.loads(captured.body))
                except ValueError:
                    continue
            payloads.extend(extract_embedded_json(rendered.html))
            assume = (
                "Copa do Mundo 2026"
                if any(s in url for s in ("copa", "world-cup", "mundial"))
                else None
            )
            quotes = harvest_many(
                payloads,
                house=self.house,
                include=settings.includes,
                exclude=settings.excludes,
                assume_competition=assume,
                url=url,
            )
            log.info(
                "render_harvest",
                house=self.house,
                url=url,
                payloads=len(payloads),
                quotes=len(quotes),
            )
            if quotes:
                return quotes
            loaded_but_empty += 1
        if loaded_but_empty:
            # página abriu mas nada reconhecido: parser precisa de ajuste, não a rede
            raise RuntimeError(
                f"{loaded_but_empty} página(s) carregaram mas nenhum mercado-alvo foi "
                "reconhecido — rode com --debug-dump e ajuste o parser"
            )
        raise RuntimeError("; ".join(errors[-2:]) or "nenhuma URL candidata configurada")
