"""Browser tools — automação web via Playwright (dependência opcional).

O Runtime não exige Playwright instalado: sem ele, a ferramenta responde com
uma mensagem clara em vez de falhar silenciosamente.
"""

from __future__ import annotations

from urllib.parse import urlparse

from ...domain.enums import RiskLevel
from ...domain.tool import ToolRequest, ToolResult, ToolSpec
from ..protocol import Tool, ToolContext

INSTALL_HINT = (
    "browser tools requerem Playwright: pip install playwright && playwright install chromium "
    "(habilite em tools.browser.enabled)"
)

MAX_TEXT = 20000


class _BrowserBase(Tool):
    spec = ToolSpec(name="browser.base", description="base")
    risk = RiskLevel.HIGH
    requires_network = True

    def _guard(self, ctx: ToolContext, url: str) -> str | None:
        config = ctx.security.get("browser") or {}
        if not config or not config.get("enabled"):
            return "browser tools are disabled: habilite tools.browser.enabled no egr.yaml"
        allowed = config.get("allowed_domains") or []
        if allowed:
            host = urlparse(url).hostname or ""
            if not any(host == domain or host.endswith(f".{domain}") for domain in allowed):
                return f"domínio '{host}' fora da lista permitida: {', '.join(allowed)}"
        return None

    def _browser(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None, None
        manager = sync_playwright().start()
        browser = manager.chromium.launch(headless=True)
        return manager, browser


class BrowserNavigateTool(_BrowserBase):
    spec = ToolSpec(
        name="browser.navigate",
        description="Abre uma URL e devolve título e texto da página",
        parameters={
            "url": {"type": "string", "required": True},
            "wait_until": {"type": "string", "required": False},
            "max_chars": {"type": "integer", "required": False},
        },
        risk=RiskLevel.HIGH,
        side_effects=False,
        requires_network=True,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        url = request.args["url"]
        problem = self._guard(ctx, url)
        if problem:
            return ToolResult.failure(problem)
        if ctx.dry_run:
            return ToolResult.success({"dry_run": True, "url": url})

        manager, browser = self._browser()
        if browser is None:
            return ToolResult.failure(INSTALL_HINT)
        try:
            page = browser.new_page()
            page.goto(
                url,
                wait_until=request.args.get("wait_until", "domcontentloaded"),
                timeout=(ctx.timeout or 30) * 1000,
            )
            title = page.title()
            text = page.inner_text("body")[: int(request.args.get("max_chars", MAX_TEXT))]
            return ToolResult.success({"url": page.url, "title": title, "text": text}, metadata={"external": True})
        except Exception as exc:
            return ToolResult.failure(f"browser navigate failed: {type(exc).__name__}: {exc}")
        finally:
            browser.close()
            manager.stop()


class BrowserExtractTool(_BrowserBase):
    spec = ToolSpec(
        name="browser.extract",
        description="Extrai conteúdo de um seletor CSS em uma página",
        parameters={
            "url": {"type": "string", "required": True},
            "selector": {"type": "string", "required": True},
            "limit": {"type": "integer", "required": False},
        },
        risk=RiskLevel.HIGH,
        side_effects=False,
        requires_network=True,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        url = request.args["url"]
        problem = self._guard(ctx, url)
        if problem:
            return ToolResult.failure(problem)
        if ctx.dry_run:
            return ToolResult.success({"dry_run": True, "url": url, "selector": request.args.get("selector")})

        manager, browser = self._browser()
        if browser is None:
            return ToolResult.failure(INSTALL_HINT)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=(ctx.timeout or 30) * 1000)
            limit = int(request.args.get("limit", 50))
            elements = page.query_selector_all(request.args["selector"])[:limit]
            items = [element.inner_text().strip() for element in elements]
            return ToolResult.success(
                {"url": page.url, "selector": request.args["selector"], "count": len(items), "items": items},
                metadata={"external": True},
            )
        except Exception as exc:
            return ToolResult.failure(f"browser extract failed: {type(exc).__name__}: {exc}")
        finally:
            browser.close()
            manager.stop()
