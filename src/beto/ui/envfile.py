"""Leitura e escrita do arquivo `.env` a partir da interface.

A interface é a fonte de verdade durante a interação; ao salvar, persistimos os
valores em `.env` (na raiz do projeto, fora do versionamento — ver `.gitignore`)
no mesmo layout do `.env.example`, com o prefixo `BETO_`.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

# .../src/beto/ui/envfile.py → raiz do projeto (onde fica o .env)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_PATH = PROJECT_ROOT / ".env"

# Campos do Settings agrupados por seção (espelha .env.example). A ordem aqui
# define a ordem das linhas geradas no .env.
ENV_GROUPS: list[tuple[str, list[str]]] = [
    (
        "Telegram (necessário só para alerter=telegram / comando run)",
        ["telegram_bot_token", "telegram_chat_id", "alerter"],
    ),
    ("Casas habilitadas (vírgula; 'mock' p/ testar o pipeline)", ["enabled_houses"]),
    (
        "Filtro de competição (substring, sem acento/caixa; vazio = todas)",
        ["competition_include", "competition_exclude"],
    ),
    ("Monitoramento", ["scrape_interval_s", "min_profit_pct", "bankroll", "dedup_ttl_s"]),
    ("Matching de eventos", ["fuzzy_threshold", "match_time_window_min"]),
    (
        "Scraping / politeness",
        [
            "request_timeout_s",
            "request_min_delay_s",
            "request_max_delay_s",
            "headless",
            "render_wait_ms",
            "proxy_server",
        ],
    ),
    ("Diversos", ["db_path", "log_level", "log_json", "debug_dump", "debug_dump_dir"]),
]

# Lista achatada na ordem de exibição — usada pela interface e pelos testes.
ALL_FIELDS: list[str] = [field for _title, fields in ENV_GROUPS for field in fields]


def _fmt(value: object) -> str:
    if isinstance(value, bool):  # antes de int: bool é subclasse de int
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def to_env_text(values: Mapping[str, object]) -> str:
    """Serializa os valores no formato `.env` (chaves `BETO_<CAMPO>`)."""
    lines = ["# Gerado pela interface beto (Streamlit). NUNCA commite o .env real."]
    for title, fields in ENV_GROUPS:
        lines.append("")
        lines.append(f"# --- {title} ---")
        for field in fields:
            lines.append(f"BETO_{field.upper()}={_fmt(values.get(field, ''))}")
    return "\n".join(lines) + "\n"


def write_env(values: Mapping[str, object], path: Path = ENV_PATH) -> Path:
    """Grava o `.env` e devolve o caminho escrito."""
    path.write_text(to_env_text(values), encoding="utf-8")
    return path
