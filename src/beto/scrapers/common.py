"""Helpers compartilhados de parsing/normalização de texto para scrapers."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from unidecode import unidecode

_WS = re.compile(r"\s+")
_LINE_RX = re.compile(r"(\d+)[.,](\d+)|(?<![\d.,])(\d+)(?![\d.,])")


def norm_text(s: str) -> str:
    """Minúsculas, sem acento, espaços colapsados — para comparações tolerantes."""
    return _WS.sub(" ", unidecode(s).lower()).strip()


def matches_competition(name: str | None, include: list[str], exclude: list[str]) -> bool:
    """Filtro de competição por substring normalizada. `include` vazio = aceita tudo."""
    if not include:
        return True
    if not name:
        return False
    n = norm_text(name)
    if any(x in n for x in exclude):
        return False
    return any(i in n for i in include)


def to_odds(value: Any) -> float | None:
    """Converte um valor para odd decimal plausível (1.01–1000), ou None."""
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, str):
            value = value.replace(",", ".")
        odds = float(value)
    except (TypeError, ValueError):
        return None
    return odds if 1.01 <= odds <= 1000 else None


def parse_line(text: str) -> float | None:
    """Extrai a linha de um texto tipo "Mais de 2.5" / "Acima 2,5" / "Total 9.5"."""
    m = _LINE_RX.search(text)
    if not m:
        return None
    if m.group(1) is not None:
        return round(float(f"{m.group(1)}.{m.group(2)}"), 2)
    return round(float(m.group(3)), 2)


def parse_start_time(value: Any) -> datetime | None:
    """Aceita ISO-8601 ou epoch (s/ms) e devolve datetime UTC tz-aware."""
    if value is None:
        return None
    if isinstance(value, int | float) and not isinstance(value, bool):
        ts = float(value)
        if ts > 1e12:  # epoch em milissegundos
            ts /= 1000.0
        if 1e9 < ts < 4e9:  # sanidade: ~2001..2096
            return datetime.fromtimestamp(ts, tz=UTC)
        return None
    if isinstance(value, str):
        raw = value.strip()
        if raw.isdigit():
            return parse_start_time(int(raw))
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    return None


_OVER_WORDS = ("mais de", "acima de", "acima", "over", "mais")
_UNDER_WORDS = ("menos de", "abaixo de", "abaixo", "under", "menos")


def ou_label(text: str) -> str | None:
    """Mapeia o nome de uma seleção O/U para o rótulo canônico over/under."""
    n = norm_text(text)
    if any(n.startswith(w) or f" {w} " in f" {n} " for w in _UNDER_WORDS):
        return "under"
    if any(n.startswith(w) or f" {w} " in f" {n} " for w in _OVER_WORDS):
        return "over"
    return None


def is_drawish(text: str) -> bool:
    n = norm_text(text)
    return n in ("x", "empate", "draw", "empate (x)") or n.startswith("empate")


def iter_strings(payload: Any) -> Iterator[str]:
    """Itera todas as strings de um JSON arbitrário (p/ achar URLs/slugs)."""
    if isinstance(payload, str):
        yield payload
    elif isinstance(payload, dict):
        for v in payload.values():
            yield from iter_strings(v)
    elif isinstance(payload, list):
        for item in payload:
            yield from iter_strings(item)
