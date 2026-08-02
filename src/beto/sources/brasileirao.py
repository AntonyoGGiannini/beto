"""Coleta do Campeonato Brasileiro (Série A/B) com odds pré-jogo 1X2.

Orquestra o cliente de `rapidapi_football`: para cada temporada pedida, busca a tabela
completa de jogos e, jogo a jogo, as odds de mandante · empate · visitante. Exporta
CSV/JSON e monta o relatório de cobertura impresso pela CLI.

Uma consequência do calendário, não do código: **odds pré-jogo só existem enquanto o
jogo não começou.** Numa temporada encerrada (2025, por exemplo) a API tende a devolver
odds vazias ou de fechamento; a coluna `pre_jogo` marca cada linha, e o relatório separa
a cobertura de jogos futuros da de jogos já disputados para isso não passar batido.
"""

from __future__ import annotations

import asyncio
import csv
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog

from beto.models import OddsQuote
from beto.sources.rapidapi_football import (
    BRASILEIRAO,
    BookOdds,
    LeagueRef,
    Match,
    MatchOdds,
    QuotaExceeded,
    RapidApiError,
    RapidApiFootball,
)

log = structlog.get_logger(__name__)

BRT = timezone(timedelta(hours=-3), "BRT")

CSV_COLUMNS = (
    "serie",
    "temporada",
    "rodada",
    "data_utc",
    "data_brasilia",
    "mandante",
    "visitante",
    "status",
    "placar",
    "pre_jogo",
    "odd_mandante",
    "odd_empate",
    "odd_visitante",
    "casa_odds",
    "n_casas",
    "overround",
    "match_id",
)


@dataclass(slots=True)
class Row:
    """Uma linha do resultado: um jogo + a cotação 1X2 escolhida."""

    match: Match
    serie: str
    odds: MatchOdds

    @property
    def chosen(self) -> BookOdds | None:
        """Cotação exibida: a de referência da API (`main`)."""
        return self.odds.main

    def as_dict(self) -> dict[str, Any]:
        book = self.chosen
        kickoff = self.match.kickoff
        return {
            "serie": self.serie.upper(),
            "temporada": self.match.season,
            "rodada": self.match.round or "",
            "data_utc": kickoff.isoformat() if kickoff else "",
            "data_brasilia": (
                kickoff.astimezone(BRT).strftime("%d/%m/%Y %H:%M") if kickoff else ""
            ),
            "mandante": self.match.home,
            "visitante": self.match.away,
            "status": "encerrado" if self.match.finished else (
                "em andamento" if self.match.started else "agendado"
            ),
            "placar": self.match.score or "",
            "pre_jogo": "sim" if not self.match.started else "nao",
            "odd_mandante": book.home if book else "",
            "odd_empate": book.draw if book else "",
            "odd_visitante": book.away if book else "",
            "casa_odds": (book.bookmaker or "") if book else "",
            "n_casas": len(self.odds.books),
            "overround": round(book.overround, 4) if book else "",
            "match_id": self.match.match_id,
        }


@dataclass(slots=True)
class CollectReport:
    """Cobertura da coleta — o que veio, o que faltou e por quê."""

    rows: list[Row] = field(default_factory=list)
    requests_made: int = 0
    cache_hits: int = 0
    errors: list[str] = field(default_factory=list)
    truncated: bool = False  # cota estourou no meio

    @property
    def with_odds(self) -> int:
        return sum(1 for r in self.rows if r.odds.available)

    @property
    def upcoming(self) -> list[Row]:
        return [r for r in self.rows if not r.match.started]

    @property
    def upcoming_with_odds(self) -> int:
        return sum(1 for r in self.upcoming if r.odds.available)

    def to_odds_quotes(self) -> list[OddsQuote]:
        """Quotes no contrato interno (só jogos pré-jogo — o resto não é apostável)."""
        out: list[OddsQuote] = []
        for row in self.upcoming:
            out.extend(row.odds.to_odds_quotes())
        return out


async def collect_season(
    api: RapidApiFootball,
    *,
    league: LeagueRef,
    year: int,
    with_odds: bool = True,
    only_upcoming: bool = False,
    report: CollectReport,
) -> None:
    """Coleta uma (liga, temporada) e acumula as linhas em `report`."""
    season = await api.resolve_season(league=league, year=year)
    try:
        matches = await api.season_matches(league=league, season=season)
    except QuotaExceeded:
        raise
    except RapidApiError as exc:
        report.errors.append(f"{league.name} {year}: {exc}")
        return
    if only_upcoming:
        matches = [m for m in matches if not m.started]

    if not with_odds:
        report.rows.extend(Row(m, league.key, MatchOdds(m)) for m in matches)
        return

    for match in matches:
        try:
            odds = await api.match_odds(match)
        except QuotaExceeded as exc:
            report.errors.append(f"cota esgotada durante {league.name} {year}: {exc}")
            report.truncated = True
            return
        if odds.error:
            report.errors.append(f"{match.home} x {match.away}: {odds.error}")
        report.rows.append(Row(match, league.key, odds))


async def collect(
    *,
    api_key: str,
    years: list[int],
    series: list[str],
    with_odds: bool = True,
    only_upcoming: bool = False,
    host: str | None = None,
    concurrency: int = 4,
    min_interval_s: float = 0.15,
    cache_dir: Path | None = None,
    matches_path: str | None = None,
    odds_path: str | None = None,
) -> CollectReport:
    """Ponto de entrada: todos os jogos das temporadas/séries pedidas, com odds 1X2."""
    report = CollectReport()
    kwargs: dict[str, Any] = {
        "concurrency": concurrency,
        "min_interval_s": min_interval_s,
        "cache_dir": cache_dir,
        "matches_path": matches_path,
        "odds_path": odds_path,
    }
    if host:
        kwargs["host"] = host
    async with RapidApiFootball(api_key, **kwargs) as api:
        for serie in series:
            league = BRASILEIRAO.get(serie)
            if league is None:
                report.errors.append(f"série desconhecida: {serie} (use a ou b)")
                continue
            for year in years:
                try:
                    await collect_season(
                        api,
                        league=league,
                        year=year,
                        with_odds=with_odds,
                        only_upcoming=only_upcoming,
                        report=report,
                    )
                except QuotaExceeded as exc:
                    report.errors.append(str(exc))
                    report.truncated = True
                    break
            if report.truncated:
                break
        report.requests_made = api.requests_made
        report.cache_hits = api.cache_hits
    report.rows.sort(
        key=lambda r: (
            r.serie,
            r.match.season,
            r.match.kickoff is None,
            r.match.kickoff or datetime.max.replace(tzinfo=UTC),
        )
    )
    return report


# --------------------------------------------------------------------------------
# saída
# --------------------------------------------------------------------------------


def write_csv(report: CollectReport, path: Path, *, sep: str = ",") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS), delimiter=sep)
        writer.writeheader()
        for row in report.rows:
            writer.writerow(row.as_dict())


def write_json(report: CollectReport, path: Path) -> None:
    payload = [
        {
            **row.as_dict(),
            "odds_por_casa": [
                {
                    "casa": b.bookmaker,
                    "mandante": b.home,
                    "empate": b.draw,
                    "visitante": b.away,
                }
                for b in row.odds.books
            ],
        }
        for row in report.rows
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def format_report(report: CollectReport, *, limit: int = 20) -> str:
    """Relatório de texto: amostra das linhas + cobertura + erros."""
    lines: list[str] = []
    header = (
        f"{'série':<6} {'temp.':<7} {'rod.':<5} {'data (BRT)':<16} "
        f"{'jogo':<42} {'1':>7} {'X':>7} {'2':>7}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for row in report.rows[:limit]:
        d = row.as_dict()
        jogo = f"{row.match.home} x {row.match.away}"
        lines.append(
            f"{d['serie']:<6} {d['temporada']:<7} {str(d['rodada'])[:5]:<5} "
            f"{d['data_brasilia']:<16} {jogo[:42]:<42} "
            f"{_num(d['odd_mandante']):>7} {_num(d['odd_empate']):>7} "
            f"{_num(d['odd_visitante']):>7}"
        )
    if len(report.rows) > limit:
        lines.append(f"... (+{len(report.rows) - limit} jogos — veja o arquivo exportado)")

    total = len(report.rows)
    upcoming = len(report.upcoming)
    lines.append("")
    lines.append(
        f"jogos: {total} | com odds: {report.with_odds} | "
        f"pré-jogo (ainda não começaram): {upcoming}, destes com odds: "
        f"{report.upcoming_with_odds}"
    )
    lines.append(
        f"requests à API: {report.requests_made} | cache: {report.cache_hits} acerto(s)"
    )
    if total and not report.with_odds:
        lines.append(
            "nenhuma odd veio: se as temporadas pedidas já terminaram isso é esperado "
            "(odds pré-jogo só existem antes do apito) — confirme com uma temporada "
            "em andamento, ou rode `--descobrir` para checar a rota de odds."
        )
    if report.truncated:
        lines.append("ATENÇÃO: coleta interrompida — cota da RapidAPI esgotada.")
    if report.errors:
        lines.append(f"erros ({len(report.errors)}), primeiros 5:")
        lines.extend(f"  - {e}" for e in report.errors[:5])
    return "\n".join(lines)


def _num(value: Any) -> str:
    return f"{value:.2f}" if isinstance(value, float) else "-"


def main_sync(**kwargs: Any) -> CollectReport:
    """Wrapper síncrono — útil em notebook/script fora do event loop."""
    return asyncio.run(collect(**kwargs))
