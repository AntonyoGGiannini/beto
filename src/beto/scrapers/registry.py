"""Registro nome → classe de scraper; montagem das casas habilitadas."""

from __future__ import annotations

from beto.scrapers.base import BookmakerScraper
from beto.scrapers.bet365 import Bet365Scraper
from beto.scrapers.betano import BetanoScraper
from beto.scrapers.betfair import BetfairScraper
from beto.scrapers.betnacional import BetnacionalScraper
from beto.scrapers.mock import MockScraper
from beto.scrapers.novibet import NovibetScraper
from beto.scrapers.sportingbet import SportingbetScraper
from beto.scrapers.superbet import SuperbetScraper
from beto.scrapers.transport import Transport

SCRAPER_CLASSES: dict[str, type[BookmakerScraper]] = {
    "mock": MockScraper,
    "novibet": NovibetScraper,
    "superbet": SuperbetScraper,
    "sportingbet": SportingbetScraper,
    "betfair": BetfairScraper,
    "betano": BetanoScraper,
    "bet365": Bet365Scraper,
    "betnacional": BetnacionalScraper,
}


def build_scrapers(
    names: list[str], transport: Transport
) -> tuple[list[BookmakerScraper], list[str]]:
    """Instancia as casas pedidas; devolve também os nomes desconhecidos."""
    scrapers: list[BookmakerScraper] = []
    unknown: list[str] = []
    for name in names:
        cls = SCRAPER_CLASSES.get(name)
        if cls is None:
            unknown.append(name)
        else:
            scrapers.append(cls(transport))
    return scrapers, unknown
