"""Transport._dump: grava debug dumps sempre em UTF-8, independente do encoding padrão
do SO (no Windows, `Path.write_text` sem `encoding=` usa cp1252 e quebra com
emoji/setas/aspas tipográficas comuns no HTML das casas de apostas — já aconteceu)."""

from __future__ import annotations

from pathlib import Path

from beto.config import Settings
from beto.scrapers.transport import Transport


def test_dump_passa_encoding_utf8_explicitamente(tmp_path, monkeypatch):
    calls: list[dict] = []
    original_write_text = Path.write_text

    def spy_write_text(self, data, *args, **kwargs):
        calls.append(kwargs)
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy_write_text)

    settings = Settings(_env_file=None, debug_dump=True, debug_dump_dir=str(tmp_path))
    transport = Transport(settings)

    body = "odds ↑ confirmadas ✓ — lucro"
    transport._dump("betano", "https://www.betano.bet.br/sport/futebol/", body, "html")

    assert len(calls) == 1
    assert calls[0].get("encoding") == "utf-8"

    files = list((tmp_path / "betano").glob("*.html"))
    assert len(files) == 1
    assert "✓" in files[0].read_text(encoding="utf-8")
