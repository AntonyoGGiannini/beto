"""Betano (betano.bet.br) — httpx: API JSON interna que espelha as rotas das páginas.

Padrão conhecido da plataforma Kaizen: qualquer rota de página devolve o JSON usado
pelo SPA quando prefixada com `/api` (com Accept: application/json). NÃO VERIFICADO
no domínio regulado .bet.br — endpoints internos mudam sem aviso.

Se quebrar: abra o site com DevTools → Network → XHR/Fetch, localize o JSON com as
odds e ajuste BASE/SPORT_PATH abaixo; ou rode `beto collect --debug-dump` e
inspecione os payloads salvos em debug/betano/.
"""

from __future__ import annotations

import httpx
import structlog

from beto.models import OddsQuote
from beto.scrapers.base import BookmakerScraper
from beto.scrapers.common import iter_strings, norm_text
from beto.scrapers.harvest import harvest_many

log = structlog.get_logger(__name__)

BASE = "https://www.betano.bet.br"
SPORT_PATH = "/sport/futebol/"
# parâmetro observado no SPA da Kaizen para incluir ligas/eventos/mercados na resposta
REQ_PARAMS = {"req": "la,s,stnf,c,mb"}


class BetanoScraper(BookmakerScraper):
    house = "betano"
    note = "endpoint interno não verificado"

    async def scrape(self) -> list[OddsQuote]:
        settings = self.transport.settings
        landing = await self.transport.get_json(
            f"{BASE}/api{SPORT_PATH}", params=REQ_PARAMS, tag=self.house
        )
        quotes = harvest_many(
            [landing],
            house=self.house,
            include=settings.includes,
            exclude=settings.excludes,
            url=BASE + SPORT_PATH,
        )

        # segue links internos de páginas da Copa do Mundo achados no payload
        wc_paths = sorted(
            {
                s
                for s in iter_strings(landing)
                if s.startswith("/sport/futebol/")
                and "copa-do-mundo" in norm_text(s)
                and len(s) < 120
            }
        )
        for path in wc_paths[:3]:
            try:
                payload = await self.transport.get_json(
                    f"{BASE}/api{path}", params=REQ_PARAMS, tag=self.house
                )
            except httpx.HTTPError as exc:
                log.warning("betano.subpage_failed", path=path, error=str(exc))
                continue
            quotes.extend(
                harvest_many(
                    [payload],
                    house=self.house,
                    include=settings.includes,
                    exclude=settings.excludes,
                    assume_competition="Copa do Mundo 2026",
                    url=BASE + path,
                )
            )

        # dedupe entre landing e subpáginas
        unique: dict[tuple, OddsQuote] = {}
        for q in quotes:
            key = (
                norm_text(q.home_team),
                norm_text(q.away_team),
                q.market_type,
                q.line,
            )
            unique.setdefault(key, q)
        return list(unique.values())
