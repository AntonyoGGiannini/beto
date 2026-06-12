"""Classe base dos adaptadores de casas + isolamento de falhas."""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field

from beto.models import OddsQuote
from beto.scrapers.transport import Transport


@dataclass(slots=True)
class ScrapeResult:
    """Resultado isolado de uma rodada de um scraper — falha nunca propaga exceção."""

    house: str
    quotes: list[OddsQuote] = field(default_factory=list)
    ok: bool = True
    error: str | None = None
    duration_s: float = 0.0
    note: str | None = None  # ex.: "endpoint não verificado", "dados fictícios"

    @property
    def n_events(self) -> int:
        return len({q.event_fingerprint() for q in self.quotes})


class BookmakerScraper(abc.ABC):
    """Adaptador plugável de uma casa.

    Cada subclasse escolhe o transporte (httpx para JSON interno, Playwright para
    páginas JS-pesadas) e é responsável por mapear o texto do site para os rótulos
    canônicos de `beto.models`.
    """

    house: str = "?"
    note: str | None = None

    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    @abc.abstractmethod
    async def scrape(self) -> list[OddsQuote]:
        """Busca + parseia + normaliza. Pode levantar exceção — o chamador isola."""

    async def safe_scrape(self) -> ScrapeResult:
        t0 = time.perf_counter()
        try:
            quotes = await self.scrape()
            return ScrapeResult(
                self.house,
                quotes,
                ok=True,
                duration_s=time.perf_counter() - t0,
                note=self.note,
            )
        except Exception as exc:  # noqa: BLE001 — fronteira intencional de isolamento
            msg = f"{type(exc).__name__}: {exc}"
            return ScrapeResult(
                self.house,
                [],
                ok=False,
                error=msg[:300],
                duration_s=time.perf_counter() - t0,
                note=self.note,
            )
