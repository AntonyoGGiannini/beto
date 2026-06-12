"""Smoke test da interface: o script roda e renderiza as 4 abas sem exceção.

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
    # sidebar + título principal
    assert any("beto" in t.value for t in app.title)
    # as quatro abas existem
    tab_labels = {t.label for t in app.tabs}
    assert {"⚙️ Configuração", "📡 Coleta", "💰 Surebets", "📨 Telegram"} <= tab_labels
    # botões-âncora de cada aba de ação
    button_labels = {b.label for b in app.button}
    assert "💾 Salvar em .env" in button_labels
    assert "📡 Rodar coleta agora" in button_labels
    assert "💰 Procurar surebets" in button_labels
