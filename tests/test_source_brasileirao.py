"""RapidAPI (Free API Live Football Data): parsers de jogos e odds 1X2 — offline.

Nenhum teste acessa a rede: o cliente é exercitado contra um `httpx.MockTransport`
e os parsers contra as fixtures em `tests/fixtures/rapidapi_*.json`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from beto.models import MarketType
from beto.sources import brasileirao
from beto.sources.rapidapi_football import (
    BRASILEIRAO,
    Match,
    MatchOdds,
    QuotaExceeded,
    RapidApiError,
    RapidApiFootball,
    extract_1x2,
    parse_matches,
)

FIXTURES = Path(__file__).parent / "fixtures"
MATCHES_PAYLOAD = json.loads((FIXTURES / "rapidapi_league_matches.json").read_text())
ODDS_PAYLOAD = json.loads((FIXTURES / "rapidapi_match_odds.json").read_text())
SERIE_A = BRASILEIRAO["a"]


# ------------------------------- parser de jogos -------------------------------


def test_parse_matches_le_o_shape_da_api():
    matches = parse_matches(MATCHES_PAYLOAD, league=SERIE_A, season="2026")

    # o terceiro nó da fixture não tem id — é descartado, não vira jogo fantasma
    assert [m.match_id for m in matches] == ["4506311", "4506310"]  # ordenado por data

    futuro = next(m for m in matches if m.match_id == "4506310")
    assert (futuro.home, futuro.away) == ("Flamengo", "Palmeiras")
    assert futuro.round == "1"
    assert futuro.kickoff == datetime(2026, 8, 9, 21, 0, tzinfo=UTC)
    assert futuro.league_id == 268
    assert futuro.finished is False
    # a fixture traz "score" antes do apito — placar só vale depois que o jogo começa
    assert futuro.score is None

    passado = next(m for m in matches if m.match_id == "4506311")
    assert passado.finished is True
    assert passado.started is True
    assert passado.score == "0 - 0"


def test_started_usa_o_relogio_quando_nao_ha_flag():
    antigo = Match(
        match_id="1",
        league_id=268,
        league="Série A",
        season="2025",
        round="10",
        kickoff=datetime(2025, 5, 1, 20, 0, tzinfo=UTC),
        home="Grêmio",
        away="Internacional",
        finished=False,
    )
    assert antigo.started is True


# ------------------------------- parser de odds -------------------------------


def test_extract_1x2_pega_todas_as_casas_e_ignora_primeiro_tempo():
    books = extract_1x2(ODDS_PAYLOAD, home="Flamengo", away="Palmeiras")

    assert [b.bookmaker for b in books] == ["Betano", "Bet365"]
    betano = books[0]
    assert (betano.home, betano.draw, betano.away) == (1.95, 3.55, 4.10)
    # dict com chaves 1/x/2 é lido igual a uma lista de seleções nomeadas
    assert (books[1].home, books[1].draw, books[1].away) == (2.05, 3.40, 3.90)
    # o mercado "1st Half result" tem 3 seleções mas NÃO é 1X2 de tempo integral
    assert all(b.home != 2.9 for b in books)


def test_overround_e_melhor_odd():
    odds = MatchOdds(
        match=parse_matches(MATCHES_PAYLOAD, league=SERIE_A, season="2026")[1],
        books=tuple(extract_1x2(ODDS_PAYLOAD, home="Flamengo", away="Palmeiras")),
    )
    assert odds.main is not None and odds.main.bookmaker == "Betano"
    melhor = odds.best
    assert melhor is not None
    assert (melhor.home, melhor.draw, melhor.away) == (2.05, 3.55, 4.10)
    assert odds.main.overround > 1  # margem da casa


def test_conversao_para_odds_quote():
    match = parse_matches(MATCHES_PAYLOAD, league=SERIE_A, season="2026")[1]
    odds = MatchOdds(match, tuple(extract_1x2(ODDS_PAYLOAD, home="Flamengo", away="Palmeiras")))
    quotes = odds.to_odds_quotes()

    assert {q.house for q in quotes} == {"Betano", "Bet365"}
    q = quotes[0]
    assert q.market_type is MarketType.MATCH_1X2
    assert q.outcome_labels() == frozenset({"home", "draw", "away"})
    assert q.source_event_id == "4506310"
    assert q.league == "Campeonato Brasileiro Série A"


def test_payload_sem_odds_nao_inventa_nada():
    assert extract_1x2({"status": "success", "response": []}, home="A", away="B") == []


# --------------------------------- cliente ---------------------------------


def _client(handler, **kwargs) -> RapidApiFootball:
    api = RapidApiFootball("chave-de-teste", min_interval_s=0.0, **kwargs)
    api._client = httpx.AsyncClient(  # noqa: SLF001 — injeção de transporte no teste
        transport=httpx.MockTransport(handler), base_url=api.base_url
    )
    return api


async def test_cliente_resolve_a_rota_de_jogos_e_ignora_as_que_falham():
    chamadas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request.url.path)
        if request.url.path == "/football-get-all-matches-by-league":
            return httpx.Response(404, json={"message": "not found"})
        if request.url.path == "/football-league-matches":
            return httpx.Response(200, json=MATCHES_PAYLOAD)
        return httpx.Response(200, json={"status": "success", "response": {}})

    async with _client(handler) as api:
        matches = await api.season_matches(league=SERIE_A, season="2026")
        assert len(matches) == 2
        # a rota resolvida fica fixada: a segunda coleta não re-testa candidatos
        await api.season_matches(league=SERIE_A, season="2026")

    assert chamadas == [
        "/football-get-all-matches-by-league",
        "/football-league-matches",
        "/football-league-matches",
    ]


async def test_temporadas_sao_consultadas_uma_vez_por_liga():
    """Coletar N temporadas não pode re-testar as rotas de seasons N vezes (cota)."""
    chamadas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request.url.path)
        return httpx.Response(404, json={})

    async with _client(handler) as api:
        assert await api.resolve_season(league=SERIE_A, year=2025) == "2025"
        assert await api.resolve_season(league=SERIE_A, year=2026) == "2026"

    assert chamadas.count("/football-league-seasons") == 1


async def test_cliente_erra_claro_quando_nenhuma_rota_serve():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "response": {}})

    async with _client(handler) as api:
        with pytest.raises(RapidApiError, match="descobrir"):
            await api.season_matches(league=SERIE_A, season="2026")


async def test_cota_esgotada_interrompe_em_vez_de_insistir():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="You have exceeded the MONTHLY quota")

    async with _client(handler) as api:
        with pytest.raises(QuotaExceeded):
            await api.season_matches(league=SERIE_A, season="2026")


async def test_odds_de_jogo_sem_cotacao_nao_derruba_a_coleta():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    match = parse_matches(MATCHES_PAYLOAD, league=SERIE_A, season="2026")[0]
    async with _client(handler) as api:
        odds = await api.match_odds(match)
    assert odds.available is False
    assert odds.error is not None


async def test_cache_em_disco_evita_segunda_chamada(tmp_path):
    chamadas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request.url.path)
        return httpx.Response(200, json=ODDS_PAYLOAD)

    match = parse_matches(MATCHES_PAYLOAD, league=SERIE_A, season="2026")[1]
    async with _client(handler, cache_dir=tmp_path) as api:
        primeira = await api.match_odds(match)
        segunda = await api.match_odds(match)

    assert len(chamadas) == 1
    assert api.cache_hits == 1
    assert primeira.books == segunda.books


# ------------------------------- coleta/export -------------------------------


async def test_collect_exporta_csv_com_as_tres_odds(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in ("/football-league-matches", "/football-get-all-matches-by-league"):
            return httpx.Response(200, json=MATCHES_PAYLOAD)
        if path.endswith("odds") or "odds" in path:
            return httpx.Response(200, json=ODDS_PAYLOAD)
        return httpx.Response(404, json={})

    real_init = RapidApiFootball.__init__

    def fake_init(self, api_key, **kwargs):
        kwargs["min_interval_s"] = 0.0
        real_init(self, api_key, **kwargs)
        self._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url=self.base_url
        )

    monkeypatch.setattr(RapidApiFootball, "__init__", fake_init)

    report = await brasileirao.collect(
        api_key="chave-de-teste", years=[2026], series=["a"], cache_dir=None
    )

    assert len(report.rows) == 2
    assert report.with_odds == 2
    assert report.upcoming_with_odds == 1  # só o jogo de 2026 ainda não começou

    destino = tmp_path / "brasileirao.csv"
    brasileirao.write_csv(report, destino)
    linhas = destino.read_text(encoding="utf-8-sig").splitlines()
    assert linhas[0].split(",")[:3] == ["serie", "temporada", "rodada"]
    assert "Flamengo" in destino.read_text(encoding="utf-8-sig")
    assert "1.95" in destino.read_text(encoding="utf-8-sig")

    brasileirao.write_json(report, tmp_path / "brasileirao.json")
    payload = json.loads((tmp_path / "brasileirao.json").read_text(encoding="utf-8"))
    fla = next(p for p in payload if p["mandante"] == "Flamengo")
    assert fla["odds_por_casa"][0] == {
        "casa": "Betano",
        "mandante": 1.95,
        "empate": 3.55,
        "visitante": 4.1,
    }

    # só jogos pré-jogo viram quotes para o motor de arbitragem
    quotes = report.to_odds_quotes()
    assert {q.home_team for q in quotes} == {"Flamengo"}
    assert "pré-jogo" in brasileirao.format_report(report)
