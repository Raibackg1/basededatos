"""
Browser Agent & Visual Token Inspector para Pumpfun / Solana DEX.

Permite al Cockpit y al Copilot IA interactuar directamente con navegadores
para inspeccionar contratos, liquidez y gráficos en DexScreener / Pump.fun / Solscan.

Implementa el comando `/browser <mint o url>` sin mentiras y usando Playwright con
el navegador Chrome o Edge nativo de Windows ya instalado en el sistema.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_SCREENSHOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "cockpit",
    "runtime",
    "last_browser_snap.png",
)


def _find_system_browser() -> Optional[str]:
    """Localiza el ejecutable de Chrome o Edge en el sistema Windows."""
    candidates = [
        os.environ.get("CHROME_PATH", ""),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.environ.get("EDGE_PATH_X86", ""),
        os.environ.get("EDGE_PATH", ""),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
        os.environ.get("BRAVE_PATH", ""),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


class BrowserAgent:
    """Agente visual, interceptor de red JSON y auditor web para tokens memecoin."""

    def __init__(self, screenshot_path: str = DEFAULT_SCREENSHOT, store: Optional[Any] = None):
        self.screenshot_path = screenshot_path
        self.browser_exe = _find_system_browser()
        self.store = store
        self.last_intercepted_payloads: Dict[str, Any] = {}

    @staticmethod
    def _resolve_target_url_and_mint(mint_or_url: str) -> tuple[str, str, str]:
        """Devuelve (target_url, extracted_mint, inferred_chain)."""
        raw = mint_or_url.strip()
        if raw.startswith("http://") or raw.startswith("https://"):
            chain = "bsc" if ("/bsc/" in raw.lower() or "0x" in raw.lower()) else "sol"
            parts = [p for p in raw.rstrip("/").split("/") if p]
            cand = parts[-1].split("?")[0].split(":")[0] if parts else ""
            return raw, cand, chain
        if raw.startswith("0x") and len(raw) >= 40:
            return f"https://dexscreener.com/bsc/{raw}", raw, "bsc"
        return f"https://dexscreener.com/solana/{raw}", raw, "sol"

    def inspect_token(self, mint_or_url: str, timeout_seconds: int = 15) -> Dict[str, Any]:
        """Abre y audita la página de un token, interceptando JSON de red y generando el Dossier GMGN."""
        t0 = time.time()
        target_url, extracted_mint, inferred_chain = self._resolve_target_url_and_mint(mint_or_url)

        dossier_dict: Optional[Dict[str, Any]] = None
        if extracted_mint and len(extracted_mint) >= 16:
            try:
                from .gmgn_dossier_scanner import GMGNDossierScanner
                scanner = GMGNDossierScanner(store=self.store)
                dossier_dict = scanner.scan_token_live(extracted_mint, chain=inferred_chain).to_dict()
            except Exception as exc:
                logger.debug("Fallback en BrowserAgent DossierScanner: %s", exc)

        if not self.browser_exe:
            return {
                "ok": False,
                "error": "No se detectó Google Chrome ni Microsoft Edge en las rutas estándar.",
                "url": target_url,
                "mint": extracted_mint,
                "chain": inferred_chain,
                "dossier": dossier_dict,
                "elapsed_seconds": round(time.time() - t0, 2),
            }

        intercepted: Dict[str, Any] = {}
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    executable_path=self.browser_exe,
                    headless=True,
                    args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
                )
                context = browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = context.new_page()
                page.set_default_timeout(timeout_seconds * 1000)

                def _on_response(resp: Any) -> None:
                    try:
                        u = resp.url.lower()
                        ctype = (resp.headers.get("content-type") or "").lower()
                        if "application/json" not in ctype:
                            return
                        if any(k in u for k in ("token", "holders", "security", "pairs", "smartmoney", "kline", "quotation")):
                            body = resp.json()
                            intercepted[u[:120]] = body
                    except Exception:
                        pass

                page.on("response", _on_response)

                logger.info("BrowserAgent navegando a: %s", target_url)
                response = page.goto(target_url, wait_until="domcontentloaded")

                # Espera 1.5s para que carguen elementos reactivos y peticiones XHR JSON
                page.wait_for_timeout(1500)

                title = page.title()
                os.makedirs(os.path.dirname(self.screenshot_path), exist_ok=True)
                page.screenshot(path=self.screenshot_path, full_page=False)

                try:
                    text_content = page.inner_text("body")[:1500]
                except Exception:
                    text_content = ""

                browser.close()
                self.last_intercepted_payloads = intercepted

                return {
                    "ok": True,
                    "url": target_url,
                    "mint": extracted_mint,
                    "chain": inferred_chain,
                    "title": title,
                    "status_code": response.status if response else 200,
                    "screenshot_saved": os.path.exists(self.screenshot_path),
                    "screenshot_path": self.screenshot_path,
                    "text_excerpt": text_content.strip()[:600],
                    "intercepted_json_count": len(intercepted),
                    "dossier": dossier_dict,
                    "elapsed_seconds": round(time.time() - t0, 2),
                }

        except Exception as exc:
            logger.error("Error en BrowserAgent: %s", exc)
            return {
                "ok": False,
                "error": str(exc),
                "url": target_url,
                "mint": extracted_mint,
                "chain": inferred_chain,
                "dossier": dossier_dict,
                "elapsed_seconds": round(time.time() - t0, 2),
            }

