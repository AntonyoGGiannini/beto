"""CLI do beto: collect (cobertura) · scan (surebets, uma rodada) · run (monitor)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys

from beto.config import Settings
from beto.logging_conf import setup_logging


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--houses",
        help="casas separadas por vírgula (padrão: BETO_ENABLED_HOUSES; ex.: mock)",
    )
    parser.add_argument(
        "--debug-dump",
        action="store_true",
        help="salva payloads brutos em debug/<casa>/ para depurar parsers",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="beto",
        description="Detector de arbitragem (surebets) em casas de aposta brasileiras.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_collect = sub.add_parser(
        "collect", help="raspa todas as casas e imprime o relatório de cobertura"
    )
    _add_common(p_collect)

    p_scan = sub.add_parser(
        "scan", help="coleta + detecta surebets e imprime (rodada única)"
    )
    _add_common(p_scan)
    p_scan.add_argument("--min-profit", type=float, help="lucro mínimo %% (padrão: env)")
    p_scan.add_argument("--bankroll", type=float, help="banca p/ cálculo de stakes")

    p_run = sub.add_parser(
        "run", help="monitor contínuo com alertas (Telegram ou console)"
    )
    _add_common(p_run)
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="força alertas no console (ignora Telegram)",
    )

    p_ui = sub.add_parser("ui", help="abre a interface web (Streamlit)")
    p_ui.add_argument("--port", type=int, default=8501, help="porta do servidor (padrão 8501)")

    p_br = sub.add_parser(
        "brasileirao",
        help="jogos do Campeonato Brasileiro + odds 1X2 via RapidAPI (exporta CSV/JSON)",
    )
    p_br.add_argument(
        "--temporadas",
        default="2025,2026",
        help="anos separados por vírgula (padrão: 2025,2026)",
    )
    p_br.add_argument(
        "--serie", default="a", help="séries separadas por vírgula: a, b (padrão: a)"
    )
    p_br.add_argument("--csv", help="arquivo CSV de saída (ex.: brasileirao.csv)")
    p_br.add_argument("--json", dest="json_out", help="arquivo JSON de saída (odds por casa)")
    p_br.add_argument("--sep", default=",", help="separador do CSV (padrão: ,)")
    p_br.add_argument(
        "--sem-odds",
        action="store_true",
        help="só a tabela de jogos (1 request por temporada, não gasta cota com odds)",
    )
    p_br.add_argument(
        "--so-futuros",
        action="store_true",
        help="ignora jogos já iniciados — odds pré-jogo só existem antes do apito",
    )
    p_br.add_argument("--limite", type=int, default=20, help="linhas impressas (padrão 20)")
    p_br.add_argument(
        "--sem-cache",
        action="store_true",
        help="não lê/grava o cache de odds em disco (BETO_RAPIDAPI_CACHE_DIR)",
    )
    p_br.add_argument(
        "--descobrir",
        action="store_true",
        help="testa as rotas candidatas da API com a sua chave e imprime o que cada uma devolve",
    )
    return parser


def _launch_ui(port: int) -> None:
    import importlib.util
    import os
    import subprocess
    from pathlib import Path

    if importlib.util.find_spec("streamlit") is None:
        raise SystemExit(
            "Streamlit não está instalado. Rode `uv sync --extra ui` "
            "(ou `pip install 'beto[ui]'`) e tente de novo."
        )
    # Railway/Render/Fly.io injetam PORT; respeita se o usuário não passou --port explícito
    effective_port = int(os.environ.get("PORT", port))
    app_path = Path(__file__).resolve().parent / "ui" / "app.py"
    subprocess.run(  # noqa: S603 — argumentos fixos, sem entrada do usuário no shell
        [
            sys.executable, "-m", "streamlit", "run", str(app_path),
            "--server.port", str(effective_port),
            "--server.address", "0.0.0.0",
            "--server.headless", "true",
        ],
        check=False,
    )


def _csv_list(value: str) -> list[str]:
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def _run_brasileirao(args: argparse.Namespace, settings: Settings) -> None:
    from pathlib import Path

    from beto.sources import brasileirao
    from beto.sources.rapidapi_football import (
        BRASILEIRAO,
        RapidApiError,
        RapidApiFootball,
    )

    if not settings.rapidapi_key:
        raise SystemExit(
            "Falta a chave da RapidAPI. Pegue em rapidapi.com/Creativesdev/api/"
            "free-api-live-football-data (botão Subscribe → chave em X-RapidAPI-Key) e "
            "defina BETO_RAPIDAPI_KEY no .env."
        )

    series = _csv_list(args.serie)
    try:
        years = [int(y) for y in _csv_list(args.temporadas)]
    except ValueError:
        raise SystemExit("--temporadas aceita apenas anos, ex.: --temporadas 2025,2026") from None

    cache_dir = None if args.sem_cache else Path(settings.rapidapi_cache_dir)

    if args.descobrir:
        league = BRASILEIRAO.get(series[0], BRASILEIRAO["a"])

        async def probe() -> None:
            async with RapidApiFootball(
                settings.rapidapi_key or "",
                host=settings.rapidapi_host,
                concurrency=1,
                min_interval_s=settings.rapidapi_min_interval_s,
            ) as api:
                matches = []
                with contextlib.suppress(RapidApiError):
                    matches = await api.season_matches(league=league, season=str(years[0]))
                results = await api.discover(
                    league_id=league.league_id,
                    season=str(years[0]),
                    match_id=matches[0].match_id if matches else None,
                )
            print(f"\nDESCOBERTA DE ROTAS — {league.name} (leagueid={league.league_id})\n")
            print(f"{'rota':<44} {'HTTP':>5}  detalhe")
            print("-" * 100)
            for r in results:
                print(f"{r.path:<44} {str(r.status or '-'):>5}  {r.detail[:48]}")
            if not matches:
                print(
                    "\nNenhum jogo lido — sem match_id, as rotas de odds não foram testadas."
                )
            print(
                "\nFixe as rotas que funcionaram em BETO_RAPIDAPI_MATCHES_PATH e "
                "BETO_RAPIDAPI_ODDS_PATH para pular a descoberta nas próximas rodadas."
            )

        asyncio.run(probe())
        return

    report = asyncio.run(
        brasileirao.collect(
            api_key=settings.rapidapi_key,
            years=years,
            series=series,
            with_odds=not args.sem_odds,
            only_upcoming=args.so_futuros,
            host=settings.rapidapi_host,
            concurrency=settings.rapidapi_concurrency,
            min_interval_s=settings.rapidapi_min_interval_s,
            cache_dir=cache_dir,
            matches_path=settings.rapidapi_matches_path,
            odds_path=settings.rapidapi_odds_path,
        )
    )
    label = "/".join(s.upper() for s in series)
    print(
        f"\nCAMPEONATO BRASILEIRO — série {label} — "
        f"temporadas {', '.join(str(y) for y in years)}\n"
    )
    print(brasileirao.format_report(report, limit=args.limite))
    if args.csv:
        brasileirao.write_csv(report, Path(args.csv), sep=args.sep)
        print(f"\nCSV: {args.csv}")
    if args.json_out:
        brasileirao.write_json(report, Path(args.json_out))
        print(f"JSON: {args.json_out}")


def _settings_from(args: argparse.Namespace) -> Settings:
    overrides: dict[str, object] = {}
    if getattr(args, "debug_dump", False):
        overrides["debug_dump"] = True
    if getattr(args, "min_profit", None) is not None:
        overrides["min_profit_pct"] = args.min_profit
    if getattr(args, "bankroll", None) is not None:
        overrides["bankroll"] = args.bankroll
    if getattr(args, "dry_run", False):
        overrides["alerter"] = "console"
    return Settings(**overrides)  # type: ignore[arg-type]


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    if args.command == "ui":
        _launch_ui(args.port)
        return

    settings = _settings_from(args)
    setup_logging(settings.log_level, settings.log_json)

    if args.command == "brasileirao":
        _run_brasileirao(args, settings)
        return

    houses = (
        [h.strip().lower() for h in args.houses.split(",") if h.strip()]
        if args.houses
        else None
    )

    from beto import orchestrator
    from beto.report import format_coverage_report

    competition = settings.competition_include.split(",")[0].strip().title() or "todas"
    label = f"{competition} — futebol"

    if args.command == "collect":
        results = asyncio.run(orchestrator.collect_once(settings, houses))
        print("\n" + format_coverage_report(results, competition_label=label))
        return

    if args.command == "scan":
        from beto.alerting.formatting import format_alert_text

        results, _events, opportunities = asyncio.run(
            orchestrator.scan_once(settings, houses)
        )
        print("\n" + format_coverage_report(results, competition_label=label))
        if not opportunities:
            print(
                f"\nNenhuma surebet ≥ {settings.min_profit_pct:.2f}% nesta rodada."
            )
        for opp in opportunities:
            print("\n" + "=" * 64)
            print(format_alert_text(opp))
        return

    if args.command == "run":
        try:
            asyncio.run(orchestrator.run_loop(settings, houses))
        except KeyboardInterrupt:
            print("\nencerrado.", file=sys.stderr)


if __name__ == "__main__":
    main()
