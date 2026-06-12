"""Smoke test da interface: o script roda e renderiza as 5 abas sem exceção.

Usa o harness oficial do Streamlit (AppTest) — não sobe servidor nem rede.
Se o Streamlit não estiver instalado (extra `ui`/`dev`), o teste é pulado.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "src" / "beto" / "ui" / "app.py")


def test_app_renderiza_sem_excecao():
    app = AppTest.from_file(APP).run(timeout=30)
    assert not app.exception
    assert any("beto" in t.value for t in app.title)
    tab_labels = {t.label for t in app.tabs}
    assert {
        "⚙️ Configuração", "📡 Coleta", "💰 Surebets", "📨 Telegram", "🔄 Monitor"
    } <= tab_labels


def test_coleta_mock_pelo_app():
    app = AppTest.from_file(APP).run(timeout=60)
    app.multiselect[0].set_value(["mock"]).run(timeout=60)
    next(b for b in app.button if b.label == "📡 Rodar coleta agora").click().run(timeout=60)
    assert not app.exception
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Coletadas"] == "1/1"
    assert metrics["Quotes"] == "4"


def test_monitor_inicia_e_para():
    """Clica em Iniciar, verifica estado, para — sem rede (usa mock)."""
    app = AppTest.from_file(APP).run(timeout=30)

    # garante que só o mock está selecionado
    app.multiselect[0].set_value(["mock"]).run(timeout=30)

    # inicia o monitor
    start_btn = next(b for b in app.button if b.label == "▶️ Iniciar monitor")
    app = start_btn.click().run(timeout=30)
    assert not app.exception

    # verifica que o botão mudou para "Parar"
    btn_labels = {b.label for b in app.button}
    assert "🛑 Parar monitor" in btn_labels

    # para
    stop_btn = next(b for b in app.button if b.label == "🛑 Parar monitor")
    app = stop_btn.click().run(timeout=30)
    assert not app.exception
