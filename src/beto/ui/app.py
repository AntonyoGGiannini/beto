"""Interface web do beto (Streamlit).

Execute com `beto ui` (ou `streamlit run src/beto/ui/app.py`).

Quatro abas: Configuração (casas, filtros, arbitragem, matching, scraping),
Coleta (relatório de cobertura), Surebets (detecção numa rodada) e Telegram
(descobrir chat_id e enviar alerta de teste).
"""

from __future__ import annotations

import asyncio
from typing import Any

import streamlit as st

from beto.alerting.formatting import market_label, outcome_label
from beto.config import ALL_HOUSES, Settings
from beto.logging_conf import setup_logging
from beto.report import format_coverage_report
from beto.ui.demo import demo_opportunity
from beto.ui.envfile import ENV_PATH, to_env_text, write_env
from beto.ui.telegram_api import discover_chats, send_alert

HOUSE_INFO: dict[str, str] = {
    "mock": "dados fictícios — sempre coleta (teste do pipeline)",
    "betano": "httpx → JSON interno (Kaizen)",
    "sportingbet": "httpx → CDS API (Entain)",
    "novibet": "Playwright + colheita heurística",
    "superbet": "Playwright + colheita heurística",
    "betfair": "Playwright + colheita heurística",
    "betnacional": "Playwright + colheita heurística",
    "bet365": "stub — Cloudflare avançado (fase posterior)",
}
HOUSE_OPTIONS: list[str] = ["mock", *ALL_HOUSES]
LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]


def _brl(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _run_async(coro: Any) -> Any:
    """Roda uma corrotina mesmo se a thread do Streamlit já tiver um loop ativo."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None and running.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def _use_discovered_chat() -> None:
    """Callback do botão: copia o chat escolhido para o campo Chat ID.

    Roda ANTES do rerun, então pode escrever no estado do widget `cfg_chat_id`.
    """
    labels = st.session_state.get("discovered_labels", {})
    pick = st.session_state.get("discovered_pick")
    if pick in labels:
        st.session_state.cfg_chat_id = labels[pick]


def _init_state() -> None:
    """Carrega a configuração atual (do .env, se houver) para o session_state."""
    if st.session_state.get("_initialized"):
        return
    s = Settings(_env_file=str(ENV_PATH))
    st.session_state.cfg_token = s.telegram_bot_token or ""
    st.session_state.cfg_chat_id = s.telegram_chat_id or ""
    st.session_state.cfg_alerter = s.alerter
    st.session_state.cfg_houses = s.houses
    st.session_state.cfg_inc = s.competition_include
    st.session_state.cfg_exc = s.competition_exclude
    st.session_state.cfg_interval = int(s.scrape_interval_s)
    st.session_state.cfg_min_profit = float(s.min_profit_pct)
    st.session_state.cfg_bankroll = float(s.bankroll)
    st.session_state.cfg_dedup_ttl = int(s.dedup_ttl_s)
    st.session_state.cfg_fuzzy = int(s.fuzzy_threshold)
    st.session_state.cfg_window = int(s.match_time_window_min)
    st.session_state.cfg_timeout = float(s.request_timeout_s)
    st.session_state.cfg_min_delay = float(s.request_min_delay_s)
    st.session_state.cfg_max_delay = float(s.request_max_delay_s)
    st.session_state.cfg_headless = bool(s.headless)
    st.session_state.cfg_render_wait = int(s.render_wait_ms)
    st.session_state.cfg_db_path = s.db_path
    st.session_state.cfg_log_level = s.log_level if s.log_level in LOG_LEVELS else "INFO"
    st.session_state.cfg_log_json = bool(s.log_json)
    st.session_state.cfg_debug_dump = bool(s.debug_dump)
    st.session_state.cfg_debug_dump_dir = s.debug_dump_dir
    st.session_state._initialized = True


def _current_values() -> dict[str, object]:
    ss = st.session_state
    return {
        "telegram_bot_token": ss.cfg_token or "",
        "telegram_chat_id": ss.cfg_chat_id or "",
        "alerter": ss.cfg_alerter,
        "enabled_houses": ",".join(ss.cfg_houses),
        "competition_include": ss.cfg_inc,
        "competition_exclude": ss.cfg_exc,
        "scrape_interval_s": ss.cfg_interval,
        "min_profit_pct": ss.cfg_min_profit,
        "bankroll": ss.cfg_bankroll,
        "dedup_ttl_s": ss.cfg_dedup_ttl,
        "fuzzy_threshold": ss.cfg_fuzzy,
        "match_time_window_min": ss.cfg_window,
        "request_timeout_s": ss.cfg_timeout,
        "request_min_delay_s": ss.cfg_min_delay,
        "request_max_delay_s": ss.cfg_max_delay,
        "headless": ss.cfg_headless,
        "render_wait_ms": ss.cfg_render_wait,
        "db_path": ss.cfg_db_path,
        "log_level": ss.cfg_log_level,
        "log_json": ss.cfg_log_json,
        "debug_dump": ss.cfg_debug_dump,
        "debug_dump_dir": ss.cfg_debug_dump_dir,
    }


def _build_settings() -> Settings:
    """Settings a partir do que está na tela (ignora o .env durante a interação)."""
    values = dict(_current_values())
    values["telegram_bot_token"] = values["telegram_bot_token"] or None
    values["telegram_chat_id"] = values["telegram_chat_id"] or None
    if not values["enabled_houses"]:
        values["enabled_houses"] = "mock"
    return Settings(_env_file=None, **values)


def _competition_label(settings: Settings) -> str:
    head = settings.competition_include.split(",")[0].strip().title()
    return f"{head or 'Todas'} — futebol"


# --------------------------------------------------------------------------- #
# Abas
# --------------------------------------------------------------------------- #
def _tab_config() -> None:
    st.subheader("Casas")
    st.multiselect(
        "Casas a monitorar",
        options=HOUSE_OPTIONS,
        key="cfg_houses",
        help="'mock' gera dados fictícios e sempre coleta — ótimo para validar o pipeline.",
    )
    st.dataframe(
        [{"casa": h, "estratégia": HOUSE_INFO[h]} for h in HOUSE_OPTIONS],
        hide_index=True,
        width="stretch",
    )

    st.subheader("Filtro de competição")
    st.caption("Substrings sem acento/caixa, separadas por vírgula. Include vazio = todas.")
    st.text_input("Incluir (include)", key="cfg_inc")
    st.text_input("Excluir (exclude)", key="cfg_exc")

    st.subheader("Arbitragem")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.number_input("Lucro mínimo (%)", min_value=0.0, max_value=50.0, step=0.1,
                        key="cfg_min_profit")
    with col2:
        st.number_input("Banca (R$)", min_value=0.0, step=100.0, key="cfg_bankroll")
    with col3:
        st.number_input("Dedup TTL (s)", min_value=0, step=60, key="cfg_dedup_ttl",
                        help="Janela para não re-alertar a mesma surebet.")

    st.subheader("Matching de eventos")
    col4, col5 = st.columns(2)
    with col4:
        st.slider("Limiar fuzzy", min_value=50, max_value=100, key="cfg_fuzzy",
                  help="Quão parecidos os nomes dos times precisam ser para casar (rapidfuzz).")
    with col5:
        st.number_input("Janela de horário (min)", min_value=0, step=5, key="cfg_window")

    st.subheader("Scraping / politeness")
    col6, col7, col8 = st.columns(3)
    with col6:
        st.number_input("Intervalo do loop (s)", min_value=5, step=5, key="cfg_interval")
        st.number_input("Timeout (s)", min_value=1.0, step=1.0, key="cfg_timeout")
    with col7:
        st.number_input("Delay mín. (s)", min_value=0.0, step=0.1, key="cfg_min_delay")
        st.number_input("Delay máx. (s)", min_value=0.0, step=0.1, key="cfg_max_delay")
    with col8:
        st.number_input("Render wait (ms)", min_value=0, step=500, key="cfg_render_wait")
        st.checkbox("Headless", key="cfg_headless")

    with st.expander("Avançado"):
        st.text_input("Caminho do banco (SQLite)", key="cfg_db_path")
        st.selectbox("Nível de log", LOG_LEVELS, key="cfg_log_level")
        st.checkbox("Log em JSON", key="cfg_log_json")
        st.checkbox("Debug dump (salvar payloads brutos)", key="cfg_debug_dump")
        st.text_input("Diretório de debug dump", key="cfg_debug_dump_dir")

    st.divider()
    col_save, col_dl = st.columns([1, 1])
    with col_save:
        if st.button("💾 Salvar em .env", type="primary", width="stretch"):
            path = write_env(_current_values())
            st.success(f"Configuração salva em `{path}`.")
    with col_dl:
        st.download_button(
            "⬇️ Baixar .env",
            data=to_env_text(_current_values()),
            file_name=".env",
            width="stretch",
        )


def _coverage_rows(results: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for r in sorted(results, key=lambda x: (x.ok, x.house), reverse=True):
        status = "FALHOU" if not r.ok else ("OK" if r.quotes else "SEM DADOS")
        rows.append(
            {
                "casa": r.house,
                "status": status,
                "eventos": r.n_events,
                "quotes": len(r.quotes),
                "tempo (s)": round(r.duration_s, 1),
                "detalhe": r.error or r.note or "—",
            }
        )
    return rows


def _tab_collect() -> None:
    st.caption(
        "Roda uma coleta única em todas as casas selecionadas e mostra o relatório "
        "de cobertura — exatamente quais coletaram."
    )
    if st.button("📡 Rodar coleta agora", type="primary"):
        settings = _build_settings()
        setup_logging(settings.log_level, settings.log_json)
        from beto import orchestrator

        with st.spinner("Coletando das casas selecionadas…"):
            try:
                st.session_state.collect_results = _run_async(
                    orchestrator.collect_once(settings)
                )
                st.session_state.collect_label = _competition_label(settings)
            except Exception as exc:  # noqa: BLE001 — mostrar a falha na UI
                st.error(f"Falha na coleta: {type(exc).__name__}: {exc}")

    results = st.session_state.get("collect_results")
    if not results:
        st.info("Nenhuma coleta ainda. Clique em **Rodar coleta agora**.")
        return

    collected = [r for r in results if r.ok and r.quotes]
    c1, c2, c3 = st.columns(3)
    c1.metric("Coletadas", f"{len(collected)}/{len(results)}")
    c2.metric("Quotes", sum(len(r.quotes) for r in results))
    c3.metric("Eventos", sum(r.n_events for r in results))
    st.dataframe(_coverage_rows(results), hide_index=True, width="stretch")
    with st.expander("Relatório em texto (como no terminal)"):
        st.code(
            format_coverage_report(
                results,
                competition_label=st.session_state.get("collect_label", "—"),
            )
        )


def _render_opportunity(opp: Any) -> None:
    when = (
        opp.start_time.strftime("%d/%m %H:%M UTC") if opp.start_time else "horário não informado"
    )
    with st.container(border=True):
        head, body = st.columns([1, 4])
        head.metric("Lucro", f"+{opp.profit_pct:.2f}%")
        body.markdown(f"**{opp.home_team} x {opp.away_team}**")
        body.caption(
            f"{opp.league or '—'} · {when} · {market_label(opp.market_type, opp.line)}"
        )
        st.dataframe(
            [
                {
                    "resultado": outcome_label(leg.outcome_label, opp.line),
                    "casa": leg.house,
                    "odd": f"{leg.odds:.2f}",
                    "stake": _brl(leg.stake),
                    "retorno": _brl(leg.guaranteed_return),
                }
                for leg in opp.legs
            ],
            hide_index=True,
            width="stretch",
        )
        st.caption(
            f"Banca {_brl(opp.bankroll)} · retorno garantido ≈ "
            f"{_brl(opp.bankroll / opp.arb_sum)} — odds mudam rápido, confirme antes de apostar."
        )


def _tab_scan() -> None:
    st.caption("Coleta + casa eventos entre as casas + roda o motor de arbitragem.")
    if st.button("💰 Procurar surebets", type="primary"):
        settings = _build_settings()
        setup_logging(settings.log_level, settings.log_json)
        from beto import orchestrator

        with st.spinner("Coletando e procurando arbitragem…"):
            try:
                results, events, opps = _run_async(orchestrator.scan_once(settings))
                st.session_state.scan_results = results
                st.session_state.scan_events = len(events)
                st.session_state.scan_opps = opps
                st.session_state.scan_min = settings.min_profit_pct
            except Exception as exc:  # noqa: BLE001 — mostrar a falha na UI
                st.error(f"Falha no scan: {type(exc).__name__}: {exc}")

    if "scan_opps" not in st.session_state:
        st.info("Nenhum scan ainda. Clique em **Procurar surebets**.")
        return

    opps = st.session_state.scan_opps
    results = st.session_state.scan_results
    c1, c2, c3 = st.columns(3)
    c1.metric("Surebets", len(opps))
    c2.metric("Eventos casados", st.session_state.scan_events)
    c3.metric("Casas OK", f"{sum(1 for r in results if r.ok)}/{len(results)}")

    if not opps:
        st.warning(
            f"Nenhuma surebet ≥ {st.session_state.scan_min:.2f}% nesta rodada. "
            "Ajuste o lucro mínimo na aba Configuração ou habilite mais casas."
        )
    for opp in opps:
        _render_opportunity(opp)


def _tab_telegram() -> None:
    st.caption(
        "Configure o bot e envie um alerta de teste. O monitor contínuo (`beto run`) "
        "usa estas credenciais quando o modo de alerta é **telegram**."
    )
    st.warning(
        "Trate o token como senha. Ele fica só no `.env` local (ignorado pelo git). "
        "Se o token vazar, gere outro com `/revoke` no @BotFather."
    )
    st.text_input("Token do bot", type="password", key="cfg_token",
                  help="Crie o bot no @BotFather.")
    st.text_input("Chat ID", key="cfg_chat_id", help="Descubra abaixo, ou com @userinfobot.")
    st.selectbox("Modo de alerta", ["console", "telegram"], key="cfg_alerter")

    token = st.session_state.cfg_token.strip()
    chat_id = st.session_state.cfg_chat_id.strip()

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("🔎 Descobrir chat_id", width="stretch"):
            if not token:
                st.error("Informe o token primeiro.")
            else:
                try:
                    chats = discover_chats(token)
                    st.session_state.discovered = chats
                    if not chats:
                        st.warning(
                            "Nenhuma conversa recente. Envie /start ao seu bot no "
                            "Telegram e tente de novo."
                        )
                except Exception as exc:  # noqa: BLE001 — mostrar a falha na UI
                    st.error(f"Falhou: {type(exc).__name__}: {exc}")
    with col_b:
        if st.button("📨 Enviar alerta de teste", type="primary", width="stretch"):
            if not (token and chat_id):
                st.error("Informe token e chat_id.")
            else:
                try:
                    send_alert(token, chat_id, demo_opportunity(st.session_state.cfg_bankroll))
                    st.success("Alerta de teste enviado! Confira o Telegram.")
                except Exception as exc:  # noqa: BLE001 — mostrar a falha na UI
                    st.error(f"Falhou: {type(exc).__name__}: {exc}")

    discovered = st.session_state.get("discovered")
    if discovered:
        labels = {
            f"{c.get('title') or c.get('username') or c.get('first_name', '?')} "
            f"(id {c['id']})": str(c["id"])
            for c in discovered
        }
        st.session_state.discovered_labels = labels
        st.selectbox("Conversas encontradas", list(labels), key="discovered_pick")
        st.button("Usar este chat_id", on_click=_use_discovered_chat)


def main() -> None:
    st.set_page_config(page_title="beto — surebets", page_icon="🟢", layout="wide")
    _init_state()

    with st.sidebar:
        st.title("🟢 beto")
        st.caption("Detector de surebets entre casas brasileiras.")
        st.metric("Casas ativas", len(st.session_state.cfg_houses))
        st.metric("Lucro mínimo", f"{st.session_state.cfg_min_profit:.2f}%")
        st.metric("Modo de alerta", st.session_state.cfg_alerter)
        st.divider()
        st.caption(
            "⚠️ Uso pessoal/educacional. Scraping pode violar os Termos de Uso das "
            "casas; alertas são indicações, não garantias. Odds mudam em segundos."
        )

    st.title("beto — detector de surebets")
    tab_cfg, tab_collect, tab_scan, tab_tg = st.tabs(
        ["⚙️ Configuração", "📡 Coleta", "💰 Surebets", "📨 Telegram"]
    )
    with tab_cfg:
        _tab_config()
    with tab_collect:
        _tab_collect()
    with tab_scan:
        _tab_scan()
    with tab_tg:
        _tab_telegram()


main()
