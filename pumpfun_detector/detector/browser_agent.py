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
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


class BrowserAgent:
    """Agente visual y auditor web para tokens memecoin."""

    def __init__(self, screenshot_path: str = DEFAULT_SCREENSHOT):
        self.screenshot_path = screenshot_path
        self.browser_exe = _find_system_browser()

    def inspect_token(self, mint_or_url: str, timeout_seconds: int = 15) -> Dict[str, Any]:
        """Abre y audita la página de un token en DexScreener o Pump.fun."""
        t0 = time.time()
        mint_or_url = mint_or_url.strip()

        if mint_or_url.startswith("http://") or mint_or_url.startswith("https://"):
            target_url = mint_or_url
        else:
            # Por defecto busca en DexScreener Solana
            target_url = f"https://dexscreener.com/solana/{mint_or_url}"

        if not self.browser_exe:
            return {
                "ok": False,
                "error": "No se detectó Google Chrome ni Microsoft Edge en las rutas estándar.",
                "url": target_url,
                "elapsed_seconds": round(time.time() - t0, 2),
            }

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

                logger.info("BrowserAgent navegando a: %s", target_url)
                response = page.goto(target_url, wait_until="domcontentloaded")

                # Espera 1.5s para que carguen elementos reactivos
                page.wait_for_timeout(1500)

                title = page.title()
                os.makedirs(os.path.dirname(self.screenshot_path), exist_ok=True)
                page.screenshot(path=self.screenshot_path, full_page=False)

                # Extraer texto visible relevante (primeros 1500 caracteres)
                try:
                    text_content = page.inner_text("body")[:1500]
                except Exception:
                    text_content = ""

                browser.close()

                return {
                    "ok": True,
                    "url": target_url,
                    "title": title,
                    "status_code": response.status if response else 200,
                    "screenshot_saved": os.path.exists(self.screenshot_path),
                    "screenshot_path": self.screenshot_path,
                    "text_excerpt": text_content.strip()[:600],
                    "elapsed_seconds": round(time.time() - t0, 2),
                }

        except Exception as exc:
            logger.error("Error en BrowserAgent: %s", exc)
            return {
                "ok": False,
                "error": str(exc),
                "url": target_url,
                "elapsed_seconds": round(time.time() - t0, 2),
            }
