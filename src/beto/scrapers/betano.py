"""Betano (betano.bet.br) — httpx: API JSON interna que espelha as rotas das páginas.

Padrão conhecido da plataforma Kaizen: qualquer rota de página devolve o JSON usado
pelo SPA quando prefixada com `/api` (com Accept: application/json). NÃO VERIFICADO
no domínio regulado .bet.br — endpoints internos mudam sem aviso.

Se quebrar: abra o site com DevTools → Network → XHR/Fetch, localize o JSON com as
odds e ajuste BASE/SPORT_PATH abaixo; ou rode `beto collect --debug-dump` e
inspecione os payloads salvos em debug/betano/.
"""

from __future__ import annotations

import contextlib
import json

import httpx
import structlog

from beto.models import OddsQuote
from beto.scrapers.base import BookmakerScraper
from beto.scrapers.common import iter_strings, norm_text
from beto.scrapers.harvest import extract_embedded_json, harvest_many

log = structlog.get_logger(__name__)

BASE = "https://www.betano.bet.br"
SPORT_PATH = "/sport/futebol/"
# parâmetro observado no SPA da Kaizen para incluir ligas/eventos/mercados na resposta
REQ_PARAMS = {"req": "la,s,stnf,c,mb"}
# headers que imitam um browser real — evitam 403 no endpoint /api/
_BROWSER_HEADERS = {
    "Referer": BASE + SPORT_PATH,
    "Origin": BASE,
    "X-Requested-With": "XMLHttpRequest",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}
_BLOCKED = (403, 429, 451)


def _harvest_render(
    rendered: object, include: list[str], exclude: list[str], url: str
) -> list[OddsQuote]:
    """Colhe quotes de um RenderResult (fallback Playwright)."""
    from beto.scrapers.transport import RenderResult  # import tardio

    if not isinstance(rendered, RenderResult):
        return []
    payloads: list[object] = []
    for c in rendered.captured:
        with contextlib.suppress(ValueError):
            payloads.append(json.loads(c.body))
    payloads.extend(extract_embedded_json(rendered.html))
    return harvest_many(
        payloads,
        house="betano",
        include=include,
        exclude=exclude,
        assume_competition="Copa do Mundo 2026",
        url=url,
    )


class BetanoScraper(BookmakerScraper):
    house = "betano"
    note = "endpoint interno não verificado"

    async def _get_json_or_render(self, url: str) -> tuple[object | None, list[OddsQuote]]:
        """Tenta httpx com headers de browser; se bloqueado (403/429/451), usa Playwright."""
        try:
            payload = await self.transport.get_json(
                url, params=REQ_PARAMS, headers=_BROWSER_HEADERS, tag=self.house
            )
            return payload, []
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in _BLOCKED:
                raise
            log.warning(
                "betano.httpx_blocked_fallback_playwright",
                status=exc.response.status_code,
                url=url,
            )
        rendered = await self.transport.render_capture(BASE + SPORT_PATH, tag=self.house)
        s = self.transport.settings
        quotes = _harvest_render(rendered, s.includes, s.excludes, url)
        return None, quotes

    async def scrape(self) -> list[OddsQuote]:
        settings = self.transport.settings
        landing, render_quotes = await self._get_json_or_render(f"{BASE}/api{SPORT_PATH}")

        if landing is None:
            # Playwright já colheu tudo que encontrou
            return render_quotes

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
                    f"{BASE}/api{path}",
                    params=REQ_PARAMS,
                    headers=_BROWSER_HEADERS,
                    tag=self.house,
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
