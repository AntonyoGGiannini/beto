"""Betano (betano.bet.br) — httpx: API JSON interna que espelha as rotas das páginas.

Padrão conhecido da plataforma Kaizen: qualquer rota de página devolve o JSON usado
pelo SPA quando prefixada com `/api` (com Accept: application/json). NÃO VERIFICADO
no domínio regulado .bet.br — endpoints internos mudam sem aviso.

Estratégia (parser dedicado + fallbacks, tudo diagnosticado):
1. httpx no endpoint /api com headers de browser;
2. se bloqueado (403/429/451), Playwright renderiza e captura os XHR;
3. `parse_kaizen` (dedicado ao shape blocks→events→markets) primeiro;
4. `harvest_report` (heurístico) como rede de segurança.

Se quebrar: abra o site com DevTools → Network → XHR/Fetch, localize o JSON com as
odds e ajuste BASE/SPORT_PATH; ou rode `beto collect --debug-dump betano`.
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from beto.models import OddsQuote, Sport
from beto.scrapers.base import BookmakerScraper
from beto.scrapers.common import iter_strings, matches_competition, norm_text, parse_start_time
from beto.scrapers.harvest import (
    RawSelection,
    _get_name,  # helper interno reaproveitado
    _get_price,
    classify_market,
    extract_embedded_json,
    harvest_report,
    map_market,
)

log = structlog.get_logger(__name__)

BASE = "https://www.betano.bet.br"
SPORT_PATH = "/sport/futebol/"
ASSUME = "Copa do Mundo 2026"
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
_EVENT_LIST_KEYS = ("events", "fixtures", "items", "rows")


def _collect_event_nodes(node: Any, out: list[dict[str, Any]]) -> None:
    """Coleta nós que têm nome com separador de times e uma lista de mercados (shape Kaizen)."""
    if isinstance(node, dict):
        name = _get_name(node)
        has_markets = any(isinstance(node.get(k), list) for k in ("markets", "optionMarkets"))
        if name and has_markets and any(sep in name for sep in (" - ", " x ", " vs ")):
            out.append(node)
        else:
            for v in node.values():
                _collect_event_nodes(v, out)
    elif isinstance(node, list):
        for item in node:
            _collect_event_nodes(item, out)


def parse_kaizen(
    payload: Any,
    *,
    house: str,
    include: list[str],
    exclude: list[str],
    scraped_at: datetime | None = None,
) -> list[OddsQuote]:
    """Parser dedicado do shape Kaizen (blocks → events → markets → selections)."""
    scraped_at = scraped_at or datetime.now(UTC)
    nodes: list[dict[str, Any]] = []
    _collect_event_nodes(payload, nodes)
    quotes: list[OddsQuote] = []
    seen: set[tuple[Any, ...]] = set()
    for ev in nodes:
        name = _get_name(ev) or ""
        home = away = None
        for sep in (" - ", " x ", " vs "):
            if sep in name:
                home, away = (p.strip() for p in name.split(sep, 1))
                break
        if not (home and away):
            continue
        league = None
        for k in ("leagueName", "competitionName", "tournamentName", "league", "competition"):
            v = ev.get(k)
            league = v.get("name") if isinstance(v, dict) else v
            if isinstance(league, str) and league.strip():
                break
            league = None
        if not matches_competition(league, include, exclude):
            continue
        start = parse_start_time(ev.get("startTime") or ev.get("startDate"))
        source_id = str(ev["id"]) if ev.get("id") is not None else None
        markets: list[Any] = []
        for k in ("markets", "optionMarkets"):
            v = ev.get(k)
            if isinstance(v, list):
                markets.extend(v)
        for market in markets:
            if not isinstance(market, dict):
                continue
            mname = _get_name(market)
            if not mname:
                continue
            mtype = classify_market(mname)
            if mtype is None:
                continue
            raw: list[RawSelection] = []
            for sel_key in ("selections", "options", "outcomes", "results"):
                sels = market.get(sel_key)
                if isinstance(sels, list):
                    for opt in sels:
                        if not isinstance(opt, dict):
                            continue
                        oname = _get_name(opt)
                        odds = _get_price(opt)
                        if oname and odds:
                            raw.append(RawSelection(oname, odds))
                    break
            for line, outcomes in map_market(mtype, raw, mname, home, away):
                key = (norm_text(home), norm_text(away), mtype, line)
                if key in seen:
                    continue
                seen.add(key)
                quotes.append(
                    OddsQuote(
                        house=house,
                        sport=Sport.FOOTBALL,
                        league=league,
                        home_team=home,
                        away_team=away,
                        start_time=start,
                        market_type=mtype,
                        line=line,
                        outcomes=outcomes,
                        scraped_at=scraped_at,
                        source_event_id=source_id,
                    )
                )
    return quotes


class BetanoScraper(BookmakerScraper):
    house = "betano"
    note = "endpoint interno não verificado"

    async def _fetch_payloads(self) -> list[Any]:
        """httpx no /api; se bloqueado, Playwright captura os XHR. Popula http_status/bytes."""
        url = f"{BASE}/api{SPORT_PATH}"
        try:
            text = await self.transport.get_text(
                url, params=REQ_PARAMS, headers=_BROWSER_HEADERS, tag=self.house
            )
        except httpx.HTTPStatusError as exc:
            self.diag.http_status = exc.response.status_code
            if exc.response.status_code not in _BLOCKED:
                raise
            log.warning("betano.httpx_blocked_fallback_playwright", status=exc.response.status_code)
            return await self._render_payloads()

        self.diag.http_status = 200
        self.diag.fetched_bytes += len(text)
        payloads: list[Any] = []
        with contextlib.suppress(ValueError):
            landing = json.loads(text)
            payloads.append(landing)
            payloads.extend(await self._fetch_subpages(landing))
        self.diag.payloads = len(payloads)
        return payloads

    async def _render_payloads(self) -> list[Any]:
        """Fallback Playwright: renderiza a landing e junta XHR JSON + JSON embutido no HTML."""
        rendered = await self.transport.render_capture(BASE + SPORT_PATH, tag=self.house)
        self.diag.fetched_bytes += len(rendered.html)
        payloads: list[Any] = []
        for c in rendered.captured:
            with contextlib.suppress(ValueError):
                payloads.append(json.loads(c.body))
        payloads.extend(extract_embedded_json(rendered.html))
        self.diag.payloads = len(payloads)
        return payloads

    async def _fetch_subpages(self, landing: Any) -> list[Any]:
        """Segue até 3 links internos de páginas da Copa do Mundo achados no landing."""
        wc_paths = sorted(
            {
                s
                for s in iter_strings(landing)
                if s.startswith("/sport/futebol/")
                and "copa-do-mundo" in norm_text(s)
                and len(s) < 120
            }
        )
        out: list[Any] = []
        for path in wc_paths[:3]:
            try:
                text = await self.transport.get_text(
                    f"{BASE}/api{path}", params=REQ_PARAMS, headers=_BROWSER_HEADERS, tag=self.house
                )
            except httpx.HTTPError as exc:
                log.warning("betano.subpage_failed", path=path, error=str(exc))
                continue
            self.diag.fetched_bytes += len(text)
            with contextlib.suppress(ValueError):
                out.append(json.loads(text))
        return out

    async def scrape(self) -> list[OddsQuote]:
        s = self.transport.settings
        payloads = await self._fetch_payloads()

        quotes = []
        for p in payloads:
            quotes.extend(
                parse_kaizen(p, house=self.house, include=s.includes, exclude=s.excludes)
            )
        if quotes:
            self.diag.parser = "kaizen"
            self.diag.markets = len(quotes)
            return _dedup(quotes)

        # fallback heurístico (diagnosticado)
        report = harvest_report(
            payloads,
            house=self.house,
            include=s.includes,
            exclude=s.excludes,
            assume_competition=ASSUME,
            url=BASE + SPORT_PATH,
        )
        self.diag.parser = "harvest" if report.quotes else self.diag.parser
        self.diag.raw_events = report.raw_events
        self.diag.in_scope = report.in_scope
        self.diag.markets = report.markets
        if report.quotes:
            log.warning("betano.dedicated_parser_empty_fallback_harvest")
        return report.quotes


def _dedup(quotes: list[OddsQuote]) -> list[OddsQuote]:
    unique: dict[tuple[Any, ...], OddsQuote] = {}
    for q in quotes:
        key = (norm_text(q.home_team), norm_text(q.away_team), q.market_type, q.line)
        unique.setdefault(key, q)
    return list(unique.values())
