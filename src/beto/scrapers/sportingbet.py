"""Sportingbet (sportingbet.bet.br, plataforma Entain) — httpx via CDS API.

Padrão conhecido da Entain (bwin/Sportingbet/Betboo): o SPA busca fixtures em
`<base>/cds-api/bettingoffer/fixtures?x-bwin-accessid=<ID>&...`. O accessId é
público e aparece no clientconfig da página. NÃO VERIFICADO no domínio regulado
.bet.br.

Se quebrar: DevTools → Network → filtre por "cds-api", copie o accessId e os
parâmetros reais e ajuste abaixo. `offerMapping=Filtered` traz os mercados
principais (1X2/totais); escanteios podem exigir `offerMapping=All` por fixture.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from beto.models import OddsQuote, Sport
from beto.scrapers.base import BookmakerScraper
from beto.scrapers.common import matches_competition, parse_start_time, to_odds
from beto.scrapers.harvest import RawSelection, classify_market, harvest_many, map_market

log = structlog.get_logger(__name__)

PORTAL_BASES = (
    "https://www.sportingbet.bet.br",
    "https://sports.sportingbet.bet.br",
)
CLIENTCONFIG_PATHS = ("/pt-br/api/clientconfig", "/api/clientconfig", "/en/api/clientconfig")
ACCESS_RX = re.compile(r'"accessId"\s*:\s*"([^"]+)"')
SPORT_ID_FOOTBALL = "4"  # futebol na taxonomia Entain


def _val(v: Any) -> str | None:
    """Entain embrulha strings em {"value": "..."}."""
    if isinstance(v, dict):
        v = v.get("value")
    return v.strip() if isinstance(v, str) and v.strip() else None


def parse_cds_fixtures(
    data: Any,
    *,
    house: str,
    include: list[str],
    exclude: list[str],
    scraped_at: datetime | None = None,
) -> list[OddsQuote]:
    """Parser dedicado do shape `{"fixtures": [...]}` da CDS API (testável offline)."""
    scraped_at = scraped_at or datetime.now(UTC)
    fixtures = data.get("fixtures") if isinstance(data, dict) else None
    if not isinstance(fixtures, list):
        return []
    quotes: list[OddsQuote] = []
    for fx in fixtures:
        if not isinstance(fx, dict):
            continue
        league = _val((fx.get("competition") or {}).get("name"))
        if not matches_competition(league, include, exclude):
            continue
        home: str | None = None
        away: str | None = None
        participants = fx.get("participants")
        if isinstance(participants, list) and len(participants) >= 2:
            home = _val(participants[0].get("name"))
            away = _val(participants[1].get("name"))
        if not (home and away):
            name = _val(fx.get("name"))
            if name and " - " in name:
                home, away = (p.strip() for p in name.split(" - ", 1))
        if not (home and away):
            continue
        start = parse_start_time(fx.get("startDate"))
        source_id = str(fx.get("id")) if fx.get("id") is not None else None

        markets = []
        for key in ("optionMarkets", "games", "markets"):
            v = fx.get(key)
            if isinstance(v, list):
                markets.extend(v)
        for market in markets:
            if not isinstance(market, dict):
                continue
            mname = _val(market.get("name"))
            if not mname:
                continue
            mtype = classify_market(mname)
            if mtype is None:
                continue
            raw: list[RawSelection] = []
            for opt in market.get("options") or market.get("results") or []:
                if not isinstance(opt, dict):
                    continue
                oname = _val(opt.get("name"))
                price = opt.get("price")
                odds = to_odds(price.get("odds")) if isinstance(price, dict) else to_odds(
                    opt.get("odds")
                )
                if oname and odds:
                    raw.append(RawSelection(oname, odds))
            for line, outcomes in map_market(mtype, raw, mname, home, away):
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


class SportingbetScraper(BookmakerScraper):
    house = "sportingbet"
    note = "endpoint interno não verificado"

    async def _discover_access_id(self) -> tuple[str, str]:
        errors: list[str] = []
        for base in PORTAL_BASES:
            for path in CLIENTCONFIG_PATHS:
                try:
                    text = await self.transport.get_text(base + path, tag=self.house)
                except httpx.HTTPError as exc:
                    errors.append(f"{base + path} → {type(exc).__name__}")
                    continue
                m = ACCESS_RX.search(text)
                if m:
                    return base, m.group(1)
        raise RuntimeError("accessId do CDS não encontrado: " + "; ".join(errors[-3:]))

    async def scrape(self) -> list[OddsQuote]:
        settings = self.transport.settings
        base, access_id = await self._discover_access_id()
        params = {
            "x-bwin-accessid": access_id,
            "lang": "pt-br",
            "country": "BR",
            "fixtureTypes": "Standard",
            "state": "Latest",
            "offerMapping": "Filtered",
            "sportIds": SPORT_ID_FOOTBALL,
            "sortBy": "StartDate",
            "skip": "0",
            "take": "250",
        }
        data = await self.transport.get_json(
            f"{base}/cds-api/bettingoffer/fixtures", params=params, tag=self.house
        )
        quotes = parse_cds_fixtures(
            data, house=self.house, include=settings.includes, exclude=settings.excludes
        )
        if not quotes:  # shape mudou? tenta a colheitadeira genérica
            quotes = harvest_many(
                [data],
                house=self.house,
                include=settings.includes,
                exclude=settings.excludes,
            )
            if quotes:
                log.warning("sportingbet.dedicated_parser_empty_fallback_harvest")
        return quotes
