"""Settings.playwright_proxy: conversão de proxy_server (URL) p/ o formato do Playwright."""

from __future__ import annotations

from beto.config import Settings


def _settings(proxy: str | None) -> Settings:
    return Settings(_env_file=None, proxy_server=proxy)


def test_sem_proxy_retorna_none():
    assert _settings(None).playwright_proxy is None
    assert _settings("").playwright_proxy is None


def test_proxy_sem_credenciais():
    assert _settings("http://proxy.br:8080").playwright_proxy == {
        "server": "http://proxy.br:8080"
    }


def test_proxy_com_credenciais_separa_server_e_login():
    assert _settings("http://user:pass@proxy.br:3128").playwright_proxy == {
        "server": "http://proxy.br:3128",
        "username": "user",
        "password": "pass",
    }


def test_httpx_usa_a_url_inteira():
    # o httpx recebe proxy_server direto (credenciais embutidas), sem conversão
    s = _settings("http://user:pass@proxy.br:3128")
    assert s.proxy_server == "http://user:pass@proxy.br:3128"
