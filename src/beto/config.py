"""Configuração via variáveis de ambiente / arquivo .env (prefixo BETO_)."""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict

ALL_HOUSES = [
    "novibet",
    "superbet",
    "sportingbet",
    "betfair",
    "betano",
    "bet365",
    "betnacional",
]


def _split(csv: str) -> list[str]:
    return [item.strip().lower() for item in csv.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="BETO_", extra="ignore")

    # Telegram (obrigatório apenas com alerter=telegram)
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    alerter: str = "console"  # console | telegram

    enabled_houses: str = ",".join(ALL_HOUSES)

    # filtro de competição: substrings normalizadas (sem acento/caixa); include vazio = todas
    competition_include: str = "copa do mundo,world cup,fifa world cup"
    competition_exclude: str = "clube,feminin,sub-,futsal,eliminat,qualif,esoccer,e-football"

    scrape_interval_s: int = 60
    min_profit_pct: float = 0.5
    bankroll: float = 1000.0
    dedup_ttl_s: int = 1800

    fuzzy_threshold: int = 85
    match_time_window_min: int = 30

    request_timeout_s: float = 20.0
    request_min_delay_s: float = 0.8
    request_max_delay_s: float = 2.5
    headless: bool = True
    render_wait_ms: int = 8000
    # proxy opcional p/ httpx e Playwright — ex.: http://user:pass@host:porta
    # útil para sair por um IP residencial brasileiro (casas .bet.br geo-bloqueiam)
    proxy_server: str | None = None

    db_path: str = "beto.db"
    log_level: str = "INFO"
    log_json: bool = False
    debug_dump: bool = False
    debug_dump_dir: str = "debug"

    @property
    def playwright_proxy(self) -> dict[str, str] | None:
        """Converte `proxy_server` (URL com credenciais opcionais) no formato do Playwright.

        O Playwright exige `server` sem credenciais e `username`/`password` à parte;
        o httpx aceita as credenciais embutidas na URL (usa `proxy_server` direto).
        """
        if not self.proxy_server:
            return None
        parts = urlsplit(self.proxy_server)
        if not parts.hostname:
            return None
        server = f"{parts.scheme or 'http'}://{parts.hostname}"
        if parts.port:
            server += f":{parts.port}"
        proxy: dict[str, str] = {"server": server}
        # urlsplit não decodifica %XX; o Playwright quer as credenciais já decodificadas
        # (o httpx recebe a URL inteira e decodifica sozinho).
        if parts.username:
            proxy["username"] = unquote(parts.username)
        if parts.password:
            proxy["password"] = unquote(parts.password)
        return proxy

    @property
    def houses(self) -> list[str]:
        return _split(self.enabled_houses)

    @property
    def includes(self) -> list[str]:
        return _split(self.competition_include)

    @property
    def excludes(self) -> list[str]:
        return _split(self.competition_exclude)
