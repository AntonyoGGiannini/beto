"""Loop de monitoramento contínuo para a interface Streamlit.

O Streamlit é single-threaded, então o loop roda em uma thread de fundo com seu
próprio asyncio event-loop. O estado (`MonitorState`) fica no nível de módulo e
sobrevive a cada rerun da página, porque o Python re-usa o módulo importado.

Fluxo:
    monitor.start(settings)  →  thread inicia, sets running=True
    monitor.stop()           →  seta stop_event; thread encerra no final do próximo
                                sleep de 1 s (interruptível) e seta running=False
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from beto.config import Settings

_MAX_LOG = 500  # linhas mantidas em memória


@dataclass
class MonitorState:
    running: bool = False
    started_at: datetime | None = None
    cycles_done: int = 0
    alerts_sent: int = 0
    opps_found: int = 0
    log: list[str] = field(default_factory=list)
    _stop_evt: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = field(default=None)

    def append(self, msg: str) -> None:
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {msg}")
        if len(self.log) > _MAX_LOG:
            del self.log[: len(self.log) - _MAX_LOG]


_state: MonitorState | None = None
_lock = threading.Lock()


def get_state() -> MonitorState | None:
    return _state


def start(settings: Settings, houses: list[str] | None = None) -> MonitorState:
    global _state
    with _lock:
        if _state is not None and _state.running:
            return _state
        s = MonitorState(running=True, started_at=datetime.now(UTC))
        s._stop_evt.clear()
        t = threading.Thread(target=_thread_main, args=(s, settings, houses), daemon=True)
        s._thread = t
        _state = s
        t.start()
    return s


def stop() -> None:
    with _lock:
        if _state is not None:
            _state._stop_evt.set()


# --------------------------------------------------------------------------- #
# Internos
# --------------------------------------------------------------------------- #

def _thread_main(
    state: MonitorState,
    settings: Settings,
    houses: list[str] | None,
) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_async_loop(state, settings, houses))
    finally:
        loop.close()
        state.running = False


async def _async_loop(
    state: MonitorState,
    settings: Settings,
    houses: list[str] | None,
) -> None:
    from beto import orchestrator
    from beto.alerting.console import ConsoleAlerter
    from beto.alerting.telegram_bot import TelegramAlerter
    from beto.storage.repository import OpportunityRepo

    if settings.alerter == "telegram" and settings.telegram_bot_token:
        alerter: TelegramAlerter | ConsoleAlerter = TelegramAlerter(
            settings.telegram_bot_token, settings.telegram_chat_id  # type: ignore[arg-type]
        )
    else:
        alerter = ConsoleAlerter()

    repo = OpportunityRepo(settings.db_path)
    state.append(f"iniciado · alerter={alerter.name} · intervalo={settings.scrape_interval_s}s")

    try:
        while not state._stop_evt.is_set():
            t0 = datetime.now(UTC)
            try:
                results, events, opps = await orchestrator.scan_once(settings, houses)
                alerts = 0
                for opp in opps:
                    if repo.seen_recently(opp.dedup_key(), settings.dedup_ttl_s):
                        continue
                    await alerter.send_alert(opp)
                    repo.mark_seen(opp)
                    alerts += 1
                repo.record_runs(results)

                state.cycles_done += 1
                state.alerts_sent += alerts
                state.opps_found += len(opps)

                ok = sum(1 for r in results if r.ok)
                dt = (datetime.now(UTC) - t0).total_seconds()
                state.append(
                    f"ciclo {state.cycles_done} · casas {ok}/{len(results)} · "
                    f"{len(events)} eventos · {len(opps)} surebets · "
                    f"{alerts} alertas · {dt:.1f}s"
                )
            except Exception as exc:  # noqa: BLE001 — ciclo nunca mata o loop
                state.append(f"ERRO no ciclo: {type(exc).__name__}: {exc}")

            # sleep interruptível: acorda a cada segundo para checar stop_evt
            for _ in range(settings.scrape_interval_s):
                if state._stop_evt.is_set():
                    break
                await asyncio.sleep(1)
    finally:
        await alerter.aclose()
        repo.close()
        state.append("monitor encerrado.")
