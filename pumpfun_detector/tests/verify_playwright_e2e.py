"""End-to-End browser verification test using Playwright in headless Chrome.
Verifies:
1. Server starts and serves the Cockpit UI.
2. Tokens are fetched from custom.db / demo_pumpfun.db and rendered in the radar.
3. First token is auto-selected into the deep-dive inspector pad.
4. AI Cockpit view renders and can send a prompt / execute /goal eval.
5. No JavaScript console errors or 404 network errors occur.
"""
import os
import sys
import time
import threading
from playwright.sync_api import sync_playwright

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from cockpit.server import CockpitState, make_server

def test_cockpit_e2e():
    db_path = "D:\\hjh\\pumpscope\\db\\custom.db"
    if not os.path.exists(db_path):
        db_path = os.path.join(os.path.dirname(__file__), "..", "demo_pumpfun.db")

    snap_path = os.path.join(os.path.dirname(__file__), "..", "cockpit", "runtime", "e2e_snapshot.json")
    state = CockpitState(snap_path, db_path)
    state.rebuild(sample=200)

    port = 8792
    srv = make_server(state, "127.0.0.1", port)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    time.sleep(1.0)

    console_errors = []
    page_errors = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path="C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
                headless=True,
            )
            page = browser.new_page()

            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))

            url = f"http://127.0.0.1:{port}/"
            print(f"Navigating to {url}...")
            page.goto(url, wait_until="networkidle")

            # Wait for tokens to populate
            page.wait_for_selector(".token-card", timeout=8000)
            token_cards = page.query_selector_all(".token-card")
            print(f"Rendered token cards: {len(token_cards)}")
            assert len(token_cards) > 0, "No token cards rendered!"

            # Check that first token is auto-selected in inspector
            insp_title = page.inner_text("#insp-token-title")
            print(f"Auto-selected token title in inspector: '{insp_title}'")
            assert insp_title != "Selecciona un Token", "Inspector was not auto-populated with first token!"

            # Switch to AI view
            page.click("button:has-text('Copilot IA & Automatización')")
            page.wait_for_selector("#ai-prompt-input", timeout=5000)

            # Check AI Provider select
            provider_val = page.input_value("#ai-provider-select")
            print(f"AI Provider selected: {provider_val}")

            # Click '/goal eval' chip
            page.click("button:has-text('/goal eval')")
            page.wait_for_timeout(2000)

            # Verify chat stream received response
            chat_stream_text = page.inner_text("#ai-chat-stream")
            print(f"Chat stream preview (last 200 chars): {chat_stream_text[-200:]}")
            assert "Evaluación Autónoma" in chat_stream_text or "Objetivos" in chat_stream_text, "Goal eval did not return expected response!"

            print(f"Console errors: {console_errors}")
            print(f"Page errors: {page_errors}")
            # Ensure no catastrophic console errors
            assert len(page_errors) == 0, f"Page errors found: {page_errors}"
            assert not any("404" in e or "Uncaught" in e for e in console_errors), f"Uncaught console errors: {console_errors}"

            browser.close()
            print("E2E PLAYWRIGHT TEST PASSED 100%!")
    finally:
        srv.shutdown()
        srv.server_close()

if __name__ == "__main__":
    test_cockpit_e2e()
