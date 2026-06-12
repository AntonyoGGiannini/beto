"""Transporte compartilhado: httpx (JSON/HTML) e Playwright (render), com politeness.

Centraliza o que nenhum adaptador deve reinventar: limite de 1 request por vez por
host, delays aleatórios entre requests, User-Agents realistas com Accept-Language
pt-BR, retries com backoff exponencial (tenacity), timeouts duros e — opcionalmente —
dump dos payloads brutos em disco (BETO_DEBUG_DUMP=1) para depurar parsers.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from beto.config import Settings

log = structlog.get_logger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
]


@dataclass(slots=True)
class CapturedResponse:
    url: str
    body: str


@dataclass(slots=True)
class RenderResult:
    html: str
    captured: list[CapturedResponse] = field(default_factory=list)


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        # 403/401 não são transientes (bloqueio) — não insistir
        return exc.response.status_code in (408, 429, 500, 502, 503, 504)
    return False


class Transport:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: httpx.AsyncClient | None = None
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._next_allowed: dict[str, float] = {}
        self._playwright: Any = None
        self._browser: Any = None
        self._browser_lock = asyncio.Lock()
        self.dump_dir: Path | None = (
            Path(settings.debug_dump_dir) if settings.debug_dump else None
        )

    # ------------------------------ httpx ------------------------------

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                http2=True,
                follow_redirects=True,
                timeout=self.settings.request_timeout_s,
                headers={
                    "User-Agent": random.choice(USER_AGENTS),
                    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.6",
                },
            )
        return self._client

    async def _polite_wait(self, url: str) -> None:
        """No máximo 1 request por vez por host, com intervalo aleatório entre eles."""
        host = urlsplit(url).netloc
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._next_allowed.get(host, 0.0) - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            delay = random.uniform(
                self.settings.request_min_delay_s, self.settings.request_max_delay_s
            )
            self._next_allowed[host] = time.monotonic() + delay

    async def _get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        retrier = AsyncRetrying(
            retry=retry_if_exception(_retryable),
            wait=wait_exponential_jitter(initial=1, max=15),
            stop=stop_after_attempt(3),
            reraise=True,
        )

        async def fetch() -> httpx.Response:
            await self._polite_wait(url)
            resp = await self.client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            return resp

        return await retrier(fetch)

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        tag: str | None = None,
    ) -> Any:
        h = {"Accept": "application/json, text/plain, */*"}
        if headers:
            h.update(headers)
        resp = await self._get(url, params=params, headers=h)
        self._dump(tag, url, resp.text, "json")
        return resp.json()

    async def get_text(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        tag: str | None = None,
    ) -> str:
        resp = await self._get(url, params=params, headers=headers)
        self._dump(tag, url, resp.text, "html")
        return resp.text

    # ---------------------------- playwright ----------------------------

    async def _ensure_browser(self) -> Any:
        async with self._browser_lock:
            if self._browser is None:
                try:
                    from playwright.async_api import async_playwright
                except ImportError as exc:  # pragma: no cover
                    raise RuntimeError(
                        "playwright não instalado (uv sync && uv run playwright install chromium)"
                    ) from exc
                self._playwright = await async_playwright().start()
                try:
                    self._browser = await self._playwright.chromium.launch(
                        headless=self.settings.headless
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"Chromium indisponível — rode `uv run playwright install chromium` ({exc})"
                    ) from exc
            return self._browser

    async def render_capture(
        self,
        url: str,
        *,
        tag: str | None = None,
        wait_selector: str | None = None,
        wait_ms: int | None = None,
    ) -> RenderResult:
        """Carrega a página num Chromium headless (locale pt-BR, tz de São Paulo) e
        devolve o HTML final + corpos das respostas JSON (XHR) capturadas no caminho."""
        await self._polite_wait(url)
        browser = await self._ensure_browser()
        context = await browser.new_context(
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1366, "height": 850},
        )
        captured: list[CapturedResponse] = []

        async def on_response(resp: Any) -> None:
            try:
                if "json" not in resp.headers.get("content-type", ""):
                    return
                body = await resp.text()
                if len(body) > 120:
                    captured.append(CapturedResponse(resp.url, body))
            except Exception:  # noqa: BLE001 — resposta pode já ter sido descartada
                pass

        page = await context.new_page()
        page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
        try:
            main = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            if main is not None and main.status >= 400:
                # falha rápida e honesta: é bloqueio/erro do site, não parser
                raise RuntimeError(f"HTTP {main.status} ao carregar a página")
            if wait_selector:
                with contextlib.suppress(Exception):
                    await page.wait_for_selector(wait_selector, timeout=10_000)
            await page.wait_for_timeout(wait_ms or self.settings.render_wait_ms)
            html: str = await page.content()
        finally:
            await context.close()

        self._dump(tag, url, html, "html")
        for c in captured:
            self._dump(tag, c.url, c.body, "json")
        log.debug("render.done", url=url, captured=len(captured))
        return RenderResult(html=html, captured=captured)

    # ------------------------------ misc ------------------------------

    def _dump(self, tag: str | None, url: str, body: str, ext: str) -> None:
        if self.dump_dir is None or tag is None:
            return
        d = self.dump_dir / tag
        d.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(url.encode()).hexdigest()[:10]
        slug = "".join(c if c.isalnum() else "-" for c in urlsplit(url).path)[-60:]
        (d / f"{slug or 'root'}-{digest}.{ext}").write_text(
            f"<!-- {url} -->\n{body}" if ext == "html" else body
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
