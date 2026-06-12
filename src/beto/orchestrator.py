"""Orquestra o pipeline: coleta → matching → arbitragem → dedup → alerta.

`collect_once`/`scan_once` são rodadas únicas (comandos collect/scan); `run_loop`
é o monitor contínuo (comando run) — um ciclo a cada BETO_SCRAPE_INTERVAL_S, que
NUNCA morre por falha de ciclo.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from beto.arbitrage.engine import ArbOpportunity, find_all
from beto.config import Settings
from beto.matching.matcher import EventMatcher, MatchedEvent
from beto.scrapers.base import ScrapeResult
from beto.scrapers.registry import build_scrapers
from beto.scrapers.transport import Transport

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class CycleStats:
    houses_ok: int
    houses_total: int
    quotes: int
    events: int
    opportunities: int
    alerts_sent: int
    duration_s: float

    def summary(self) -> str:
        return (
            f"casas {self.houses_ok}/{self.houses_total} · {self.quotes} quotes · "
            f"{self.events} eventos · {self.opportunities} surebets · "
            f"{self.alerts_sent} alertas · {self.duration_s:.1f}s"
        )


async def collect_once(
    settings: Settings, houses: list[str] | None = None
) -> list[ScrapeResult]:
    """Raspa todas as casas habilitadas (isoladas e em paralelo)."""
    transport = Transport(settings)
    try:
        scrapers, unknown = build_scrapers(houses or settings.houses, transport)
        for name in unknown:
            log.warning("casa desconhecida ignorada", house=name)
        results = list(await asyncio.gather(*(s.safe_scrape() for s in scrapers)))
    finally:
        await transport.aclose()
    for r in results:
        log.info(
            "scraper.result",
            house=r.house,
            ok=r.ok,
            quotes=len(r.quotes),
            events=r.n_events,
            duration_s=round(r.duration_s, 2),
            error=r.error,
        )
    return results


async def scan_once(
    settings: Settings, houses: list[str] | None = None
) -> tuple[list[ScrapeResult], list[MatchedEvent], list[ArbOpportunity]]:
    """Coleta + casa eventos + roda o motor de arbitragem (sem alertar)."""
    results = await collect_once(settings, houses)
    quotes = [q for r in results if r.ok for q in r.quotes]
    matcher = EventMatcher(
        fuzzy_threshold=settings.fuzzy_threshold,
        time_window_min=settings.match_time_window_min,
    )
    events = matcher.group(quotes)
    opportunities = find_all(
        events, bankroll=settings.bankroll, min_profit_pct=settings.min_profit_pct
    )
    log.info(
        "scan.done",
        quotes=len(quotes),
        events=len(events),
        opportunities=len(opportunities),
    )
    return results, events, opportunities


async def run_loop(settings: Settings, houses: list[str] | None = None) -> None:
    """Monitor contínuo com alertas e dedup. Ctrl+C para sair."""
    from beto.alerting.console import ConsoleAlerter
    from beto.alerting.telegram_bot import CommandBot, RuntimeState, TelegramAlerter
    from beto.storage.repository import OpportunityRepo

    repo = OpportunityRepo(settings.db_path)
    state = RuntimeState(min_profit_pct=settings.min_profit_pct)

    alerter: TelegramAlerter | ConsoleAlerter
    command_bot: CommandBot | None = None
    if settings.alerter == "telegram":
        if not (settings.telegram_bot_token and settings.telegram_chat_id):
            raise SystemExit(
                "BETO_TELEGRAM_BOT_TOKEN e BETO_TELEGRAM_CHAT_ID são obrigatórios com "
                "alerter=telegram (ou use --dry-run / BETO_ALERTER=console)"
            )
        alerter = TelegramAlerter(settings.telegram_bot_token, settings.telegram_chat_id)
        command_bot = CommandBot(
            settings.telegram_bot_token, settings.telegram_chat_id, state, repo
        )
        await command_bot.start()
    else:
        alerter = ConsoleAlerter()

    log.info(
        "monitor.start",
        alerter=alerter.name,
        interval_s=settings.scrape_interval_s,
        min_profit_pct=state.min_profit_pct,
    )
    try:
        while True:
            started = datetime.now(UTC)
            try:
                results, events, opportunities = await scan_once(settings, houses)
                # threshold pode ter sido alterado em runtime via /threshold
                opportunities = [
                    o for o in opportunities if o.profit_pct >= state.min_profit_pct
                ]
                alerts_sent = 0
                for opp in opportunities:
                    if repo.seen_recently(opp.dedup_key(), settings.dedup_ttl_s):
                        continue
                    await alerter.send_alert(opp)
                    repo.mark_seen(opp)
                    alerts_sent += 1
                repo.record_runs(results)
                stats = CycleStats(
                    houses_ok=sum(1 for r in results if r.ok),
                    houses_total=len(results),
                    quotes=sum(len(r.quotes) for r in results),
                    events=len(events),
                    opportunities=len(opportunities),
                    alerts_sent=alerts_sent,
                    duration_s=(datetime.now(UTC) - started).total_seconds(),
                )
                state.last_cycle_summary = stats.summary()
                log.info("cycle.done", **{
                    "houses": f"{stats.houses_ok}/{stats.houses_total}",
                    "quotes": stats.quotes,
                    "events": stats.events,
                    "opps": stats.opportunities,
                    "alerts": stats.alerts_sent,
                    "duration_s": round(stats.duration_s, 1),
                })
            except Exception:  # noqa: BLE001 — o loop nunca morre por um ciclo ruim
                log.exception("cycle.failed")
            await asyncio.sleep(settings.scrape_interval_s)
    finally:
        if command_bot is not None:
            await command_bot.stop()
        await alerter.aclose()
        repo.close()
