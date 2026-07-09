"""Estratégia genérica para casas sem endpoint JSON mapeado: renderizar e colher.

Abre a página da competição num Chromium headless, captura todos os JSONs que o
site buscou (XHR) + os embutidos no HTML, e passa tudo pela colheitadeira
heurística (`beto.scrapers.harvest`). Tenta cada URL candidata em ordem e para na
primeira que render quotes — preenchendo o funil de diagnóstico no caminho.

Para mapear o endpoint definitivo de uma casa: rode `beto collect --debug-dump`,
inspecione os payloads salvos em debug/<casa>/ e promova o adaptador para um
parser dedicado (exemplos: betano.py, sportingbet.py).
"""

from __future__ import annotations

import json

import structlog

from beto.models import OddsQuote
from beto.scrapers.base import BookmakerScraper
from beto.scrapers.harvest import extract_embedded_json, harvest_report

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
            self.diag.fetched_bytes += len(rendered.html)
            payloads: list[object] = []
            for captured in rendered.captured:
                try:
                    payloads.append(json.loads(captured.body))
                except ValueError:
                    continue
            payloads.extend(extract_embedded_json(rendered.html))
            self.diag.payloads += len(payloads)
            assume = (
                "Copa do Mundo 2026"
                if any(s in url for s in ("copa", "world-cup", "mundial"))
                else None
            )
            report = harvest_report(
                payloads,
                house=self.house,
                include=settings.includes,
                exclude=settings.excludes,
                assume_competition=assume,
                url=url,
            )
            # guarda o funil da candidata mais "rica" vista até aqui (p/ diagnóstico honesto)
            if report.raw_events >= self.diag.raw_events:
                self.diag.raw_events = report.raw_events
                self.diag.in_scope = report.in_scope
                self.diag.markets = report.markets
            log.info(
                "render_harvest",
                house=self.house,
                url=url,
                payloads=len(payloads),
                raw_events=report.raw_events,
                quotes=len(report.quotes),
            )
            if report.quotes:
                self.diag.parser = "render+harvest"
                return report.quotes
            loaded_but_empty += 1
        if loaded_but_empty:
            # página(s) abriram: o funil no diag aponta onde secou; não estoura exceção,
            # deixa o `safe_scrape` reportar a dica (SEM DADOS diagnosticado).
            log.warning(
                "render_harvest.empty",
                house=self.house,
                pages=loaded_but_empty,
                raw_events=self.diag.raw_events,
                in_scope=self.diag.in_scope,
            )
            return []
        # nenhuma página sequer carregou: é falha de rede/bloqueio → exceção (FALHOU)
        raise RuntimeError("; ".join(errors[-2:]) or "nenhuma URL candidata configurada")
