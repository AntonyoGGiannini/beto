"""Cliente da API RapidAPI "Free API Live Football Data" (Creativesdev).

Fonte **agregada** de jogos + odds — diferente dos `scrapers/`, que raspam casas uma a
uma. Aqui existe chave, contrato e cota: o transporte é próprio (sem os delays de
politeness do scraping), com controle de concorrência, respeito a `Retry-After` e
cache em disco para não queimar cota reprocessando o mesmo evento.

Dois problemas práticos desta API, e como o módulo lida com eles:

1. **Nomes de rota variam entre revisões da API.** Em vez de fixar um caminho, cada
   operação tem uma lista de candidatos (`MATCHES_PATHS`, `ODDS_PATHS`, …); o cliente
   testa em ordem, fixa o primeiro que responder 2xx com corpo útil e reusa daí em
   diante. `BETO_RAPIDAPI_MATCHES_PATH` / `BETO_RAPIDAPI_ODDS_PATH` fixam à mão, e
   `RapidApiFootball.discover()` (CLI: `beto brasileirao --descobrir`) imprime o que
   cada candidato devolveu com a sua chave.
2. **O shape do JSON varia por rota.** Os parsers (`parse_matches`, `extract_1x2`) são
   heurísticos e navegam o JSON procurando os nós que parecem jogo/odds, no mesmo
   espírito de `scrapers/harvest.py` — em vez de assumir um caminho fixo de chaves.

Uso direto:

    async with RapidApiFootball(key) as api:
        jogos = await api.season_matches(league_id=268, season="2025")
        odds = await api.match_odds(jogos[0])
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import structlog
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from beto.models import MarketType, OddsQuote, Outcome, Sport
from beto.scrapers.common import is_drawish, norm_text, parse_start_time, to_odds

log = structlog.get_logger(__name__)

DEFAULT_HOST = "free-api-live-football-data.p.rapidapi.com"


@dataclass(frozen=True, slots=True)
class LeagueRef:
    """Uma competição na numeração da API (herdada do FotMob)."""

    key: str
    league_id: int
    name: str


# IDs do Brasileirão. NÃO VERIFICADOS ao vivo (o sandbox de desenvolvimento bloqueia
# rapidapi.com): confira com `beto brasileirao --descobrir`, que imprime o nome da
# liga que a API devolveu para cada id — e ajuste aqui se vier outra competição.
BRASILEIRAO: dict[str, LeagueRef] = {
    "a": LeagueRef("a", 268, "Campeonato Brasileiro Série A"),
    "b": LeagueRef("b", 269, "Campeonato Brasileiro Série B"),
}

# Candidatos de rota por operação, na ordem de preferência (ver docstring do módulo).
MATCHES_PATHS: tuple[str, ...] = (
    "/football-get-all-matches-by-league",
    "/football-league-matches",
    "/football-get-list-all-matches-by-league",
    "/football-get-matches-by-league",
)
ODDS_PATHS: tuple[str, ...] = (
    "/football-get-match-odds",
    "/football-get-all-odds-by-match",
    "/football-match-odds",
    "/football-get-odds",
)
SEASONS_PATHS: tuple[str, ...] = (
    "/football-get-all-seasons-by-league",
    "/football-league-seasons",
    "/football-get-league-seasons",
)
LEAGUE_PATHS: tuple[str, ...] = (
    "/football-league-detail",
    "/football-get-league-detail",
    "/football-league-info",
)


class RapidApiError(RuntimeError):
    """Falha de contrato/rede contra a API."""


class QuotaExceeded(RapidApiError):
    """Cota do plano RapidAPI esgotada — insistir só gasta tempo."""


# --------------------------------------------------------------------------------
# modelos
# --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Match:
    """Um jogo já normalizado, independente do shape que a API devolveu."""

    match_id: str
    league_id: int
    league: str
    season: str
    round: str | None
    kickoff: datetime | None  # UTC tz-aware
    home: str
    away: str
    finished: bool
    score: str | None = None

    @property
    def started(self) -> bool:
        """True se o jogo já começou (logo, as odds não são mais pré-jogo)."""
        if self.finished:
            return True
        return self.kickoff is not None and self.kickoff <= datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class BookOdds:
    """Odds 1X2 de UMA casa para um jogo (mandante · empate · visitante)."""

    bookmaker: str | None
    home: float
    draw: float
    away: float

    @property
    def overround(self) -> float:
        """Soma dos inversos: > 1 é a margem da casa; < 1 seria arbitragem na própria."""
        return 1 / self.home + 1 / self.draw + 1 / self.away


@dataclass(frozen=True, slots=True)
class MatchOdds:
    """Todas as cotações 1X2 encontradas para um jogo + a consolidação usada no relatório."""

    match: Match
    books: tuple[BookOdds, ...] = ()
    error: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.books)

    @property
    def main(self) -> BookOdds | None:
        """Cotação de referência: a primeira devolvida pela API (casa "padrão")."""
        return self.books[0] if self.books else None

    @property
    def best(self) -> BookOdds | None:
        """Melhor odd de cada resultado entre todas as casas (visão de arbitragem)."""
        if not self.books:
            return None
        return BookOdds(
            bookmaker="melhor",
            home=max(b.home for b in self.books),
            draw=max(b.draw for b in self.books),
            away=max(b.away for b in self.books),
        )

    def to_odds_quotes(self, *, scraped_at: datetime | None = None) -> list[OddsQuote]:
        """Converte para o contrato interno do beto (matching/arbitragem/alertas)."""
        ts = scraped_at or datetime.now(UTC)
        return [
            OddsQuote(
                house=book.bookmaker or "rapidapi",
                sport=Sport.FOOTBALL,
                league=self.match.league,
                home_team=self.match.home,
                away_team=self.match.away,
                start_time=self.match.kickoff,
                market_type=MarketType.MATCH_1X2,
                line=None,
                outcomes=(
                    Outcome("home", book.home),
                    Outcome("draw", book.draw),
                    Outcome("away", book.away),
                ),
                scraped_at=ts,
                source_event_id=self.match.match_id,
            )
            for book in self.books
        ]


# --------------------------------------------------------------------------------
# parsing — jogos
# --------------------------------------------------------------------------------

_ID_KEYS = ("id", "matchid", "match_id", "eventid", "event_id", "fixtureid", "gameid")
_HOME_KEYS = ("home", "hometeam", "home_team", "localteam", "team1", "homename")
_AWAY_KEYS = ("away", "awayteam", "away_team", "visitorteam", "team2", "awayname")
_NAME_KEYS = ("name", "shortname", "longname", "title", "displayname", "team", "teamname")
_ROUND_KEYS = ("round", "roundname", "matchround", "week", "stage", "gameweek")
_TIME_KEYS = ("utctime", "starttime", "kickoff", "date", "datetime", "time", "timestamp")
_SCORE_KEYS = ("scorestr", "score", "result", "ftscore")


def _keymap(node: dict[str, Any]) -> dict[str, Any]:
    """Índice das chaves do nó em minúsculas, para casar shapes camelCase/snake_case."""
    return {str(k).lower(): v for k, v in node.items()}


def _first(km: dict[str, Any], keys: Sequence[str]) -> Any:
    for k in keys:
        if k in km and km[k] is not None:
            return km[k]
    return None


def _team_name(value: Any) -> str | None:
    """Nome do time, aceitando string direta ou objeto {"id":…, "name":…}."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        km = _keymap(value)
        for k in _NAME_KEYS:
            v = km.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def iter_dicts(payload: Any) -> Iterator[dict[str, Any]]:
    """Percorre todos os dicts de um JSON arbitrário, em profundidade."""
    if isinstance(payload, dict):
        yield payload
        for v in payload.values():
            yield from iter_dicts(v)
    elif isinstance(payload, list):
        for item in payload:
            yield from iter_dicts(item)


def unwrap(payload: Any) -> Any:
    """Descasca o envelope `{"status": "success", "response": …}` da API."""
    seen = 0
    while isinstance(payload, dict) and seen < 4:
        inner = payload.get("response") or payload.get("data") or payload.get("result")
        if inner is None or isinstance(inner, str):
            break
        payload = inner
        seen += 1
    return payload


def _finished_of(km: dict[str, Any]) -> bool:
    status = km.get("status")
    if isinstance(status, dict):
        skm = _keymap(status)
        for k in ("finished", "isfinished", "ended"):
            if isinstance(skm.get(k), bool):
                return bool(skm[k])
        short = skm.get("shortname") or skm.get("reason") or skm.get("scorestr")
        if isinstance(short, str) and norm_text(short) in ("ft", "aet", "pen", "encerrado"):
            return True
    if isinstance(status, str):
        return norm_text(status) in ("finished", "ft", "encerrado", "played")
    return bool(km.get("finished") is True)


def _kickoff_of(km: dict[str, Any]) -> datetime | None:
    status = km.get("status")
    if isinstance(status, dict):
        found = _first(_keymap(status), _TIME_KEYS)
        dt = parse_start_time(found)
        if dt:
            return dt
    return parse_start_time(_first(km, _TIME_KEYS))


def _score_of(km: dict[str, Any]) -> str | None:
    for source in (km.get("status"), km):
        if isinstance(source, dict):
            value = _first(_keymap(source), _SCORE_KEYS)
            if isinstance(value, str) and any(c.isdigit() for c in value):
                return value.strip()
    home, away = km.get("home"), km.get("away")
    if isinstance(home, dict) and isinstance(away, dict):
        hs, as_ = _keymap(home).get("score"), _keymap(away).get("score")
        if isinstance(hs, int | str) and isinstance(as_, int | str):
            return f"{hs} - {as_}"
    return None


def _round_of(km: dict[str, Any]) -> str | None:
    value = _first(km, _ROUND_KEYS)
    if isinstance(value, dict):
        value = _first(_keymap(value), ("name", "round", "value"))
    if isinstance(value, int | str) and not isinstance(value, bool):
        text = str(value).strip()
        return text or None
    return None


def _match_from_node(node: dict[str, Any], *, league: LeagueRef, season: str) -> Match | None:
    """Tenta ler um nó como jogo. None se não tiver os dois times."""
    km = _keymap(node)
    home = _team_name(_first(km, _HOME_KEYS))
    away = _team_name(_first(km, _AWAY_KEYS))
    if not home or not away or norm_text(home) == norm_text(away):
        return None
    raw_id = _first(km, _ID_KEYS)
    match_id = str(raw_id).strip() if isinstance(raw_id, int | str) else ""
    if not match_id or match_id.lower() in ("none", "0"):
        return None
    finished = _finished_of(km)
    kickoff = _kickoff_of(km)
    # o payload traz placar 0/0 (ou lixo) antes do apito inicial — só reporta se começou
    started = finished or (kickoff is not None and kickoff <= datetime.now(UTC))
    return Match(
        match_id=match_id,
        league_id=league.league_id,
        league=league.name,
        season=season,
        round=_round_of(km),
        kickoff=kickoff,
        home=home,
        away=away,
        finished=finished,
        score=_score_of(km) if started else None,
    )


def parse_matches(payload: Any, *, league: LeagueRef, season: str) -> list[Match]:
    """Extrai todos os jogos de um payload da API, sem assumir onde eles estão.

    Deduplica por `match_id` e ordena por data (jogos sem data vão para o fim).
    """
    found: dict[str, Match] = {}
    for node in iter_dicts(unwrap(payload)):
        match = _match_from_node(node, league=league, season=season)
        if match is not None:
            found.setdefault(match.match_id, match)
    return sorted(
        found.values(),
        key=lambda m: (m.kickoff is None, m.kickoff or datetime.max.replace(tzinfo=UTC)),
    )


# --------------------------------------------------------------------------------
# parsing — odds 1X2
# --------------------------------------------------------------------------------

# Grupos de chaves que, juntos num mesmo dict, formam um 1X2 completo.
_TRIPLET_KEYS: tuple[tuple[str, str, str], ...] = (
    ("1", "x", "2"),
    ("home", "draw", "away"),
    ("homeodds", "drawodds", "awayodds"),
    ("oddhome", "odddraw", "oddaway"),
    ("homewin", "draw", "awaywin"),
    ("homeodd", "drawodd", "awayodd"),
    ("w1", "x", "w2"),
    ("h", "d", "a"),
)
_PROVIDER_KEYS = (
    "provider",
    "providername",
    "bookmaker",
    "bookmakername",
    "operator",
    "bookie",
    "source",
)
_MARKET_KEYS = ("market", "marketname", "markettype", "name", "title", "label", "type")
# Mercados que TAMBÉM têm três seleções e seriam confundidos com o 1X2 de tempo
# integral. Filosofia do repo: na dúvida, descarta.
_MARKET_BLOCKLIST = (
    "1st half",
    "first half",
    "2nd half",
    "second half",
    "primeiro tempo",
    "segundo tempo",
    "half time",
    "halftime",
    "intervalo",
    "ht/ft",
    "handicap",
    "asian",
    "corner",
    "escanteio",
    "card",
    "cartao",
    "extra time",
    "prorrogacao",
    "double chance",
    "chance dupla",
)
_SELECTION_LIST_KEYS = (
    "odds",
    "selections",
    "outcomes",
    "options",
    "values",
    "choices",
    "runners",
    "prices",
)
_PRICE_KEYS = ("odd", "odds", "price", "value", "decimal", "decimalodds", "coefficient")
_SELECTION_NAME_KEYS = ("name", "label", "title", "value", "selection", "outcome", "type")


def _clean_key(key: str) -> str:
    return "".join(c for c in str(key).lower() if c.isalnum())


def _provider_of(km: dict[str, Any]) -> str | None:
    for k in _PROVIDER_KEYS:
        v = km.get(k)
        name = _team_name(v)
        if name:
            return name
    return None


def _market_blocked(km: dict[str, Any]) -> bool:
    for k in _MARKET_KEYS:
        v = km.get(k)
        if isinstance(v, str):
            n = norm_text(v)
            if any(bad in n for bad in _MARKET_BLOCKLIST):
                return True
    return False


def _triplet_from_dict(node: dict[str, Any]) -> tuple[float, float, float] | None:
    """1X2 num dict com chaves nomeadas (ex.: {"1": 2.1, "x": 3.4, "2": 3.6})."""
    km = {_clean_key(k): v for k, v in node.items()}
    for a, b, c in _TRIPLET_KEYS:
        if a in km and b in km and c in km:
            odds = (to_odds(km[a]), to_odds(km[b]), to_odds(km[c]))
            if all(o is not None for o in odds):
                return odds  # type: ignore[return-value]
    return None


def _label_of(item: dict[str, Any], *, home: str, away: str) -> str | None:
    """Rótulo canônico (home/draw/away) de uma seleção, pelo nome."""
    km = _keymap(item)
    raw = None
    for k in _SELECTION_NAME_KEYS:
        v = km.get(k)
        if isinstance(v, str) and v.strip():
            raw = v.strip()
            break
    if raw is None:
        return None
    n = norm_text(raw)
    if n in ("1", "home", "mandante", "casa", "local") or n == norm_text(home):
        return "home"
    if n in ("2", "away", "visitante", "fora") or n == norm_text(away):
        return "away"
    if n in ("x", "draw") or is_drawish(raw):
        return "draw"
    return None


def _price_of(item: dict[str, Any]) -> float | None:
    km = _keymap(item)
    for k in _PRICE_KEYS:
        odds = to_odds(km.get(k))
        if odds is not None:
            return odds
    return None


def _triplet_from_seq(
    seq: Sequence[Any], *, home: str, away: str
) -> tuple[float, float, float] | None:
    """1X2 numa lista de seleções nomeadas ([{"name": "Flamengo", "odd": 1.9}, …])."""
    prices: dict[str, float] = {}
    for item in seq:
        if not isinstance(item, dict):
            return None
        label = _label_of(item, home=home, away=away)
        price = _price_of(item)
        if label and price is not None:
            prices.setdefault(label, price)
    if len(prices) == 3:
        return prices["home"], prices["draw"], prices["away"]
    return None


def _walk_odds(
    node: Any, *, home: str, away: str, provider: str | None, out: list[BookOdds]
) -> None:
    if isinstance(node, dict):
        km = _keymap(node)
        if _market_blocked(km):
            return
        prov = _provider_of(km) or provider
        triplet = _triplet_from_dict(node)
        if triplet is None:
            for key in _SELECTION_LIST_KEYS:
                value = km.get(key)
                if isinstance(value, list):
                    triplet = _triplet_from_seq(value, home=home, away=away)
                    if triplet:
                        break
        if triplet is not None:
            out.append(BookOdds(prov, *triplet))
            return  # nó já rendeu o mercado; não descer para variações dele
        for v in node.values():
            _walk_odds(v, home=home, away=away, provider=prov, out=out)
    elif isinstance(node, list):
        triplet = _triplet_from_seq(node, home=home, away=away)
        if triplet is not None:
            out.append(BookOdds(provider, *triplet))
            return
        for item in node:
            _walk_odds(item, home=home, away=away, provider=provider, out=out)


def extract_1x2(payload: Any, *, home: str, away: str) -> list[BookOdds]:
    """Todas as cotações 1X2 do payload, uma por casa. Deduplica cotações idênticas."""
    out: list[BookOdds] = []
    _walk_odds(unwrap(payload), home=home, away=away, provider=None, out=out)
    seen: set[tuple[str | None, float, float, float]] = set()
    unique: list[BookOdds] = []
    for book in out:
        key = (book.bookmaker, book.home, book.draw, book.away)
        if key not in seen:
            seen.add(key)
            unique.append(book)
    return unique


# --------------------------------------------------------------------------------
# cliente HTTP
# --------------------------------------------------------------------------------


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (408, 429, 500, 502, 503, 504)
    return False


_ERROR_STATUSES = ("error", "failure", "fail", "false")
_CACHE_STAMP = "_cached_at"  # epoch de gravação — base da validade do cache


def _plausible_envelope(payload: Any) -> bool:
    """A resposta parece um sucesso da API (ainda que sem o dado que queríamos)?

    Distingue "rota certa, jogo sem odds" de "rota errada": a segunda costuma vir como
    `{"message": "Endpoint … does not exist"}` ou `{"status": "error"}`.
    """
    if isinstance(payload, list):
        return True
    if not isinstance(payload, dict):
        return False
    status = payload.get("status")
    if isinstance(status, str) and status.lower() in _ERROR_STATUSES:
        return False
    if payload.keys() <= {"message", "messages", "error", "errors"}:
        return False
    return bool(payload)


@dataclass(slots=True)
class ProbeResult:
    """O que um caminho candidato devolveu — matéria-prima do `--descobrir`."""

    path: str
    status: int | None
    ok: bool
    detail: str


class RapidApiFootball:
    """Cliente assíncrono com resolução de rota, cota, cache e concorrência limitada."""

    def __init__(
        self,
        api_key: str,
        *,
        host: str = DEFAULT_HOST,
        timeout_s: float = 20.0,
        concurrency: int = 4,
        min_interval_s: float = 0.15,
        cache_dir: Path | None = None,
        odds_ttl_s: float = 900.0,
        matches_path: str | None = None,
        odds_path: str | None = None,
    ) -> None:
        if not api_key:
            raise RapidApiError(
                "chave RapidAPI ausente — defina BETO_RAPIDAPI_KEY (X-RapidAPI-Key)"
            )
        self.host = host
        self.base_url = f"https://{host}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout_s,
            headers={
                "X-RapidAPI-Key": api_key,
                "X-RapidAPI-Host": host,
                "Accept": "application/json",
            },
        )
        self.concurrency = max(1, concurrency)
        self._sem = asyncio.Semaphore(self.concurrency)
        self._min_interval_s = max(0.0, min_interval_s)
        self._next_at = 0.0
        self._pace_lock = asyncio.Lock()
        self.cache_dir = cache_dir
        # validade do cache de odds de jogo AINDA NÃO iniciado (preço muda o tempo todo)
        self.odds_ttl_s = max(0.0, odds_ttl_s)
        # rotas fixadas à mão têm prioridade sobre a descoberta automática
        self._resolved: dict[str, str] = {}
        self._resolve_locks: dict[str, asyncio.Lock] = {}
        self.weak_routes: set[str] = set()
        if matches_path:
            self._resolved["matches"] = matches_path
        if odds_path:
            self._resolved["odds"] = odds_path
        self._season_labels: dict[int, list[str]] = {}
        self.requests_made = 0
        self.cache_hits = 0

    async def __aenter__(self) -> RapidApiFootball:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------ transporte ------------------------------

    async def _pace(self) -> None:
        """Espaça as chamadas no tempo — RapidAPI limita requests/segundo por plano."""
        if not self._min_interval_s:
            return
        async with self._pace_lock:
            wait = self._next_at - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_at = time.monotonic() + self._min_interval_s

    async def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        """GET com retry/backoff. Levanta QuotaExceeded quando o plano estourou."""
        retrier = AsyncRetrying(
            retry=retry_if_exception(_retryable),
            wait=wait_exponential_jitter(initial=1, max=20),
            stop=stop_after_attempt(3),
            reraise=True,
        )

        async def fetch() -> httpx.Response:
            async with self._sem:
                await self._pace()
                resp = await self._client.get(path, params=params)
            self.requests_made += 1
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    await asyncio.sleep(min(int(retry_after), 60))
                if "exceeded" in resp.text.lower() or "quota" in resp.text.lower():
                    raise QuotaExceeded(f"cota RapidAPI esgotada: {resp.text[:160]}")
            if resp.status_code == 403 and "exceeded" in resp.text.lower():
                raise QuotaExceeded(f"cota/plano RapidAPI: {resp.text[:160]}")
            resp.raise_for_status()
            return resp

        try:
            resp: httpx.Response = await retrier(fetch)
        except httpx.HTTPStatusError as exc:
            raise RapidApiError(
                f"HTTP {exc.response.status_code} em {path}: {exc.response.text[:160]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RapidApiError(f"falha de rede em {path}: {exc}") from exc
        try:
            return resp.json()
        except ValueError as exc:
            raise RapidApiError(f"resposta não-JSON em {path}: {resp.text[:160]}") from exc

    # --------------------------- resolução de rota ---------------------------

    async def _resolve(
        self,
        op: str,
        candidates: Sequence[str],
        params: dict[str, str],
        probe: Any,
        *,
        allow_weak: bool = False,
    ) -> tuple[str, Any]:
        """Fixa (e memoriza) o primeiro candidato cujo payload passa em `probe`.

        `allow_weak` separa "a rota é válida" de "este jogo tem dado agora" — o caso
        das odds: um jogo encerrado (ou ainda sem mercado aberto) devolve payload vazio
        pela rota CERTA. Sem isso, cada jogo sem cotação re-testaria todos os candidatos
        e a cota evaporaria antes de chegar nos jogos futuros. Quando nenhum candidato
        traz dado mas algum respondeu com um envelope plausível, esse é fixado (com
        aviso) em vez de deixar a operação eternamente por resolver.
        """
        if op in self._resolved:
            path = self._resolved[op]
            return path, await self.get(path, params)
        # sob concorrência, só uma task descobre a rota; as demais esperam e reusam
        lock = self._resolve_locks.setdefault(op, asyncio.Lock())
        async with lock:
            if op not in self._resolved:
                return await self._discover_route(
                    op, candidates, params, probe, allow_weak=allow_weak
                )
            path = self._resolved[op]
        return path, await self.get(path, params)

    async def _discover_route(
        self,
        op: str,
        candidates: Sequence[str],
        params: dict[str, str],
        probe: Any,
        *,
        allow_weak: bool,
    ) -> tuple[str, Any]:
        errors: list[str] = []
        weak: tuple[str, Any] | None = None
        for path in candidates:
            try:
                payload = await self.get(path, params)
            except QuotaExceeded:
                raise
            except RapidApiError as exc:
                errors.append(f"{path}: {exc}")
                continue
            if probe(payload):
                self._resolved[op] = path
                log.info("rapidapi.rota_resolvida", op=op, path=path)
                return path, payload
            if weak is None and _plausible_envelope(payload):
                weak = (path, payload)
            errors.append(f"{path}: 2xx mas sem dado reconhecível")
        if allow_weak and weak is not None:
            path, payload = weak
            self._resolved[op] = path
            self.weak_routes.add(op)
            log.warning("rapidapi.rota_fixada_sem_dado", op=op, path=path)
            return path, payload
        raise RapidApiError(
            f"nenhuma rota de '{op}' respondeu com dados úteis. Tentativas: "
            + " | ".join(errors)
            + ". Rode `beto brasileirao --descobrir` e fixe a rota certa em "
            "BETO_RAPIDAPI_MATCHES_PATH / BETO_RAPIDAPI_ODDS_PATH."
        )

    async def discover(
        self, *, league_id: int, season: str, match_id: str | None
    ) -> list[ProbeResult]:
        """Testa todos os candidatos de rota com a chave real e relata o resultado."""
        plan: list[tuple[str, dict[str, str]]] = []
        for path in LEAGUE_PATHS:
            plan.append((path, {"leagueid": str(league_id)}))
        for path in SEASONS_PATHS:
            plan.append((path, {"leagueid": str(league_id)}))
        for path in MATCHES_PATHS:
            plan.append((path, {"leagueid": str(league_id), "season": season}))
        if match_id:
            for path in ODDS_PATHS:
                plan.append((path, {"eventid": match_id, "matchid": match_id}))

        results: list[ProbeResult] = []
        for path, params in plan:
            try:
                payload = await self.get(path, params)
            except QuotaExceeded:
                raise
            except RapidApiError as exc:
                text = str(exc)
                status = None
                if text.startswith("HTTP "):
                    with contextlib.suppress(ValueError):
                        status = int(text.split()[1])
                results.append(ProbeResult(path, status, False, text[:120]))
                continue
            keys = list(unwrap(payload).keys()) if isinstance(unwrap(payload), dict) else []
            n_matches = len(parse_matches(payload, league=BRASILEIRAO["a"], season=season))
            n_odds = len(extract_1x2(payload, home="", away=""))
            results.append(
                ProbeResult(
                    path,
                    200,
                    True,
                    f"jogos={n_matches} odds1x2={n_odds} chaves={keys[:6]}",
                )
            )
        return results

    # ------------------------------ operações ------------------------------

    async def season_matches(self, *, league: LeagueRef, season: str) -> list[Match]:
        """Todos os jogos da liga na temporada (rodadas passadas e futuras)."""
        params = {"leagueid": str(league.league_id), "season": season, "year": season}
        _path, payload = await self._resolve(
            "matches",
            MATCHES_PATHS,
            params,
            probe=lambda p: bool(parse_matches(p, league=league, season=season)),
        )
        matches = parse_matches(payload, league=league, season=season)
        log.info(
            "rapidapi.jogos", liga=league.name, temporada=season, jogos=len(matches)
        )
        return matches

    async def season_labels(self, *, league: LeagueRef) -> list[str]:
        """Rótulos de temporada aceitos pela API (ex.: "2025" ou "2025/2026").

        Memoizado por liga: sem isso, coletar N temporadas re-testaria os candidatos
        de rota N vezes — puro desperdício de cota.
        """
        cached = self._season_labels.get(league.league_id)
        if cached is not None:
            return cached
        self._season_labels[league.league_id] = []
        for path in SEASONS_PATHS:
            try:
                payload = await self.get(path, {"leagueid": str(league.league_id)})
            except QuotaExceeded:
                raise
            except RapidApiError:
                continue
            labels: list[str] = []
            for node in iter_dicts(unwrap(payload)):
                for value in node.values():
                    if isinstance(value, str) and value[:4].isdigit() and len(value) <= 9:
                        labels.append(value)
            if labels:
                unique = list(dict.fromkeys(labels))
                self._season_labels[league.league_id] = unique
                return unique
        return []

    async def resolve_season(self, *, league: LeagueRef, year: int) -> str:
        """Converte o ano pedido no rótulo que a API usa; cai para `str(year)`."""
        try:
            labels = await self.season_labels(league=league)
        except RapidApiError:
            labels = []
        exact = [lb for lb in labels if lb == str(year)]
        if exact:
            return exact[0]
        # temporada europeia ("2025/2026"): prefere a que COMEÇA no ano pedido
        starts = [lb for lb in labels if lb.startswith(str(year))]
        if starts:
            return starts[0]
        contains = [lb for lb in labels if str(year) in lb]
        return contains[0] if contains else str(year)

    async def match_odds(self, match: Match) -> MatchOdds:
        """Odds 1X2 do jogo (mandante · empate · visitante), com cache em disco.

        O cache tem duas políticas, porque os dois casos são diferentes: jogo já
        iniciado não muda mais de preço (cache eterno), jogo futuro muda o tempo todo
        (vale `odds_ttl_s`). E resposta vazia de jogo futuro NÃO é gravada — o mercado
        pode simplesmente ainda não ter aberto, e cachear isso congelaria o "sem odds".
        """
        # jogo já iniciado: o preço não muda mais, cache não expira
        ttl = None if match.started else self.odds_ttl_s
        cached = self._cache_read(match.match_id, ttl_s=ttl)
        if cached is not None:
            self.cache_hits += 1
            return MatchOdds(match, tuple(extract_1x2(cached, home=match.home, away=match.away)))
        params = {"eventid": match.match_id, "matchid": match.match_id}
        try:
            _path, payload = await self._resolve(
                "odds",
                ODDS_PATHS,
                params,
                probe=lambda p: bool(extract_1x2(p, home=match.home, away=match.away)),
                allow_weak=True,
            )
        except QuotaExceeded:
            raise
        except RapidApiError as exc:
            return MatchOdds(match, (), error=str(exc)[:200])
        books = tuple(extract_1x2(payload, home=match.home, away=match.away))
        if books or match.started:
            self._cache_write(match.match_id, payload)
        return MatchOdds(match, books)

    # -------------------------------- cache --------------------------------

    def _cache_path(self, match_id: str) -> Path | None:
        if self.cache_dir is None:
            return None
        safe = "".join(c if c.isalnum() else "-" for c in match_id)[:64]
        return self.cache_dir / f"odds-{safe}.json"

    def _cache_read(self, match_id: str, *, ttl_s: float | None) -> Any | None:
        """Payload em cache, ou None se ausente/ilegível/vencido. `ttl_s=None` = eterno."""
        path = self._cache_path(match_id)
        if path is None or not path.exists():
            return None
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(envelope, dict) or _CACHE_STAMP not in envelope:
            return None  # formato antigo/estranho: trata como ausente
        if ttl_s is not None:
            age = time.time() - float(envelope.get(_CACHE_STAMP) or 0.0)
            if age < 0 or age > ttl_s:
                return None
        return envelope.get("payload")

    def _cache_write(self, match_id: str, payload: Any) -> None:
        path = self._cache_path(match_id)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {_CACHE_STAMP: time.time(), "payload": payload}
        with contextlib.suppress(OSError, TypeError, ValueError):
            path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
