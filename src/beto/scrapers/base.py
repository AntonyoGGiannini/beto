"""Classe base dos adaptadores de casas + isolamento de falhas + diagnóstico.

O ponto central da reescrita: a coleta deixa de ser uma caixa-preta. Cada scraper
preenche um `ScrapeDiag` com os contadores de cada estágio do funil
(bytes → payloads → eventos → em-escopo → mercados → quotes). Quando a coleta
devolve zero, `safe_scrape` transforma esses contadores numa dica acionável — a
diferença entre "SEM DADOS" e "carregou 250 KB, 40 eventos, 0 na competição:
relaxe BETO_COMPETITION_INCLUDE".
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field

from beto.models import OddsQuote
from beto.scrapers.transport import Transport


@dataclass(slots=True)
class ScrapeDiag:
    """Funil de uma coleta: onde os eventos se perderam entre a rede e as quotes.

    Todos os contadores começam em zero; cada scraper preenche o que conseguir. O
    estágio em que o funil "seca" (o primeiro que zera) aponta a causa provável do
    `SEM DADOS` — ver `bottleneck_hint`.
    """

    parser: str | None = None          # qual estratégia produziu as quotes (kaizen/cds/harvest…)
    http_status: int | None = None     # status HTTP da busca principal, quando aplicável
    fetched_bytes: int = 0             # total de bytes brutos considerados (JSON/HTML)
    payloads: int = 0                  # nº de payloads JSON reconhecidos/parseados
    raw_events: int = 0                # eventos candidatos encontrados (com dois times)
    in_scope: int = 0                  # eventos que passaram no filtro de competição
    markets: int = 0                   # mercados-alvo (1X2/O-U) reconhecidos
    quotes: int = 0                    # quotes finais emitidas

    def merge(self, other: ScrapeDiag) -> None:
        """Acumula contadores de um sub-passo (ex.: várias URLs candidatas)."""
        self.fetched_bytes += other.fetched_bytes
        self.payloads += other.payloads
        self.raw_events += other.raw_events
        self.in_scope += other.in_scope
        self.markets += other.markets
        self.quotes += other.quotes
        if other.parser and not self.parser:
            self.parser = other.parser
        if other.http_status is not None and self.http_status is None:
            self.http_status = other.http_status

    def bottleneck_hint(self, *, includes: list[str]) -> str | None:
        """Dica acionável quando a coleta não rendeu quotes. None se rendeu."""
        if self.quotes:
            return None
        if self.http_status is not None and self.http_status >= 400:
            return f"HTTP {self.http_status} — bloqueio/erro do site (geo? proxy BR?)"
        if self.payloads == 0:
            return "resposta sem JSON reconhecível (bloqueio ou HTML puro) — rode --debug-dump"
        if self.raw_events == 0:
            return "nenhum evento reconhecido no payload (shape novo?) — --debug-dump e ajuste"
        if self.in_scope == 0:
            filtro = ", ".join(includes) if includes else "(vazio)"
            return (
                f"{self.raw_events} evento(s), 0 na competição — "
                f"relaxe BETO_COMPETITION_INCLUDE [{filtro}]"
            )
        if self.markets == 0:
            return f"{self.in_scope} evento(s) em escopo, nenhum mercado-alvo (1X2/O-U) reconhecido"
        return f"{self.markets} mercado(s) reconhecidos mas sem odds completas para arbitragem"


@dataclass(slots=True)
class ScrapeResult:
    """Resultado isolado de uma rodada de um scraper — falha nunca propaga exceção."""

    house: str
    quotes: list[OddsQuote] = field(default_factory=list)
    ok: bool = True
    error: str | None = None
    duration_s: float = 0.0
    note: str | None = None  # ex.: "endpoint não verificado", "dados fictícios"
    diag: ScrapeDiag = field(default_factory=ScrapeDiag)
    hint: str | None = None  # dica acionável quando ok mas sem quotes

    @property
    def n_events(self) -> int:
        return len({q.event_fingerprint() for q in self.quotes})

    @property
    def detail(self) -> str:
        """Coluna 'detalhe' do relatório: erro > dica de diagnóstico > nota."""
        return self.error or self.hint or self.note or "-"


class BookmakerScraper(abc.ABC):
    """Adaptador plugável de uma casa.

    Cada subclasse escolhe o transporte (httpx para JSON interno, Playwright para
    páginas JS-pesadas) e é responsável por mapear o texto do site para os rótulos
    canônicos de `beto.models`. Durante `scrape`, preenche `self.diag` com os
    contadores do funil (opcional, mas é o que dá visibilidade ao "SEM DADOS").
    """

    house: str = "?"
    note: str | None = None

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.diag = ScrapeDiag()

    @abc.abstractmethod
    async def scrape(self) -> list[OddsQuote]:
        """Busca + parseia + normaliza. Pode levantar exceção — o chamador isola."""

    async def safe_scrape(self) -> ScrapeResult:
        t0 = time.perf_counter()
        try:
            quotes = await self.scrape()
        except Exception as exc:  # noqa: BLE001 — fronteira intencional de isolamento
            msg = f"{type(exc).__name__}: {exc}"
            return ScrapeResult(
                self.house,
                [],
                ok=False,
                error=msg[:300],
                duration_s=time.perf_counter() - t0,
                note=self.note,
                diag=self.diag,
            )
        self.diag.quotes = len(quotes)
        includes = self.transport.settings.includes
        return ScrapeResult(
            self.house,
            quotes,
            ok=True,
            duration_s=time.perf_counter() - t0,
            note=self.note,
            diag=self.diag,
            hint=None if quotes else self.diag.bottleneck_hint(includes=includes),
        )
