"""CLI do beto: collect (cobertura) · scan (surebets, uma rodada) · run (monitor)."""

from __future__ import annotations

import argparse
import asyncio
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
