#!/usr/bin/env python3
"""
Live Data Collector usando Playwright para extraer datos en tiempo real
de DexScreener, Pump.fun, PumpPortal y otras fuentes DEX.
"""
from __future__ import annotations
import asyncio, json, time, hashlib, os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
from abc import ABC, abstractmethod

import sqlite3
import urllib.request
import urllib.error


@dataclass
class LiveTokenSnapshot:
    """Snapshot en vivo de un token."""
    token_id: str
    ts_ms: int
    price_usd: float
    market_cap: float
    liquidity: float
    volume_24h: float
    change_m5: float
    change_h1: float
    buys_m5: int
    sells_m5: int
    holder_count: Optional[int]
    net_buyers_1h: Optional[int]
    migration_progress: Optional[float]
    source: str  # dexscreener, pumpfun, pumpportal, helius
    latency_ms: int


class DataSource(ABC):
    """Interfaz para fuentes de datos en vivo."""
    
    @abstractmethod
    async def fetch_new_tokens(self) -> List[Dict[str, Any]]:
        """Retorna tokens nuevos detectados."""
        pass
    
    @abstractmethod
    async def fetch_token_snapshot(self, token_id: str) -> Optional[LiveTokenSnapshot]:
        """Retorna snapshot actual de un token."""
        pass
    
    @abstractmethod
    def get_name(self) -> str:
        pass


class DexScreenerSource(DataSource):
    """Fuente DexScreener via REST API real (sin mocks, fail-closed)."""

    def __init__(self, base_url: str = "https://api.dexscreener.com", timeout_s: int = 15):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.session = None

    def get_name(self) -> str:
        return "dexscreener"

    def _http_get(self, path: str, params: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
        """Petición HTTP GET real a DexScreener API. Fail-closed: retorna None si falla."""
        url = f"{self.base_url}{path}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "pumpscope-live-collector/1.0"})
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                if resp.status != 200:
                    return None
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except Exception:
            return None

    async def fetch_new_tokens(self) -> List[Dict[str, Any]]:
        """
        Obtiene tokens nuevos de DexScreener (Solana).
        Usa el endpoint de búsqueda con query "pump.fun" para encontrar
        tokens recientes de Pump.fun, y el endpoint de tokens por address
        para obtener detalles.
        """
        tokens = []

        # Buscar tokens relacionados con pump.fun en Solana
        data = self._http_get("/latest/dex/search", {"q": "pump.fun"})
        if not data or "pairs" not in data:
            return []

        pairs = data["pairs"]
        # Filtrar solo chainId = solana y limitar
        solana_pairs = [p for p in pairs if p.get("chainId") == "solana"][:50]

        now_ms = int(time.time() * 1000)
        for pair in solana_pairs:
            base_token = pair.get("baseToken", {})
            token_id = base_token.get("address", "")
            if not token_id:
                continue

            pair_created_at = pair.get("pairCreatedAt", now_ms)
            age_ms = now_ms - pair_created_at if isinstance(pair_created_at, (int, float)) else 0

            tokens.append({
                "token_id": token_id,
                "name": base_token.get("name", ""),
                "symbol": base_token.get("symbol", ""),
                "address": token_id,
                "pair_address": pair.get("pairAddress", ""),
                "price_usd": pair.get("priceUsd", 0),
                "market_cap": pair.get("marketCap", 0),
                "liquidity": pair.get("liquidity", {}).get("usd", 0) if pair.get("liquidity") else 0,
                "volume_24h": pair.get("volume", {}).get("h24", 0) if pair.get("volume") else 0,
                "change_m5": pair.get("priceChange", {}).get("m5", 0) if pair.get("priceChange") else 0,
                "change_h1": pair.get("priceChange", {}).get("h1", 0) if pair.get("priceChange") else 0,
                "buys_m5": pair.get("txns", {}).get("m5", {}).get("buys", 0) if pair.get("txns") else 0,
                "sells_m5": pair.get("txns", {}).get("m5", {}).get("sells", 0) if pair.get("txns") else 0,
                "source": "dexscreener",
                "age_ms": age_ms,
                "is_new": age_ms < 300000,
            })

        return tokens

    async def fetch_token_snapshot(self, token_id: str) -> Optional[LiveTokenSnapshot]:
        """Obtiene snapshot actual de un token por su address."""
        data = self._http_get(f"/tokens/v1/solana/{token_id}")
        if not data or not isinstance(data, list) or len(data) == 0:
            return None

        pair = data[0]
        price_usd = float(pair.get("priceUsd", 0) or 0)
        market_cap = float(pair.get("marketCap", 0) or 0)
        liquidity = 0.0
        if pair.get("liquidity"):
            liquidity = float(pair["liquidity"].get("usd", 0) or 0)
        volume_24h = 0.0
        if pair.get("volume"):
            volume_24h = float(pair["volume"].get("h24", 0) or 0)
        change_m5 = 0.0
        change_h1 = 0.0
        if pair.get("priceChange"):
            change_m5 = float(pair["priceChange"].get("m5", 0) or 0)
            change_h1 = float(pair["priceChange"].get("h1", 0) or 0)
        buys_m5 = 0
        sells_m5 = 0
        if pair.get("txns"):
            buys_m5 = int(pair["txns"].get("m5", {}).get("buys", 0) or 0)
            sells_m5 = int(pair["txns"].get("m5", {}).get("sells", 0) or 0)

        return LiveTokenSnapshot(
            token_id=token_id,
            ts_ms=int(time.time() * 1000),
            price_usd=price_usd,
            market_cap=market_cap,
            liquidity=liquidity,
            volume_24h=volume_24h,
            change_m5=change_m5,
            change_h1=change_h1,
            buys_m5=buys_m5,
            sells_m5=sells_m5,
            holder_count=None,
            net_buyers_1h=None,
            migration_progress=None,
            source="dexscreener",
            latency_ms=0,
        )


class PumpFunSource(DataSource):
    """Fuente Pump.fun via WebSocket/API."""
    
    def __init__(self):
        self.ws = None
    
    def get_name(self) -> str:
        return "pumpfun"
    
    async def fetch_new_tokens(self) -> List[Dict[str, Any]]:
        # Conectar a WebSocket de Pump.fun para nuevos tokens
        pass
    
    async def fetch_token_snapshot(self, token_id: str) -> Optional[LiveTokenSnapshot]:
        pass


class LiveCollector:
    """
    Colector principal que orquesta múltiples fuentes.
    Guarda en SQLite con tabla ticks_crudos para auditoría completa.
    """
    
    def __init__(self, db_path: str, sources: List[DataSource] = None):
        self.db_path = db_path
        self.sources = sources or []
        self.running = False
        self._init_schema()
        self.stats = {"snapshots": 0, "errors": 0, "tokens_tracked": set()}
    
    def _init_schema(self):
        schema = """
        CREATE TABLE IF NOT EXISTS ticks_crudos (
            token_id TEXT NOT NULL,
            ts_ms INTEGER NOT NULL,
            price_usd REAL,
            market_cap REAL,
            liquidity REAL,
            volume_24h REAL,
            change_m5 REAL,
            change_h1 REAL,
            buys_m5 INTEGER,
            sells_m5 INTEGER,
            holder_count INTEGER,
            net_buyers_1h INTEGER,
            migration_progress REAL,
            source TEXT NOT NULL,
            latency_ms INTEGER,
            collected_at_ms INTEGER NOT NULL,
            PRIMARY KEY (token_id, ts_ms, source)
        );
        CREATE INDEX IF NOT EXISTS idx_ticks_token_ts ON ticks_crudos(token_id, ts_ms);
        
        CREATE TABLE IF NOT EXISTS live_tokens_tracked (
            token_id TEXT PRIMARY KEY,
            first_seen_ms INTEGER NOT NULL,
            last_seen_ms INTEGER NOT NULL,
            source TEXT NOT NULL,
            metadata_json TEXT
        );
        """
        with sqlite3.connect(self.db_path) as con:
            con.executescript(schema)
    
    def add_source(self, source: DataSource):
        self.sources.append(source)
    
    async def collect_loop(self, interval_s: int = 30):
        """Loop principal de recolección."""
        self.running = True
        print(f"[LiveCollector] Iniciado con {len(self.sources)} fuentes, interval={interval_s}s")
        
        while self.running:
            start = time.time()
            
            for source in self.sources:
                try:
                    await self._collect_from_source(source)
                except Exception as e:
                    self.stats["errors"] += 1
                    print(f"[LiveCollector] Error en {source.get_name()}: {e}")
            
            elapsed = time.time() - start
            sleep_time = max(0, interval_s - elapsed)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
    
    async def _collect_from_source(self, source: DataSource):
        """Recolecta de una fuente específica."""
        # 1. Detectar tokens nuevos
        new_tokens = await source.fetch_new_tokens()
        for token in new_tokens:
            self._register_token(token, source.get_name())
        
        # 2. Actualizar snapshots de tokens trackeados
        with sqlite3.connect(self.db_path) as con:
            tokens = con.execute(
                "SELECT token_id FROM live_tokens_tracked WHERE source = ?",
                (source.get_name(),)
            ).fetchall()
        
        for (token_id,) in tokens:
            try:
                snapshot = await source.fetch_token_snapshot(token_id)
                if snapshot:
                    self._save_snapshot(snapshot)
            except Exception as e:
                print(f"[LiveCollector] Error snapshot {token_id}: {e}")
    
    def _register_token(self, token: Dict[str, Any], source: str):
        """Registra un token nuevo para tracking."""
        token_id = token.get("token_id") or token.get("mint") or token.get("address")
        if not token_id:
            return
        
        with sqlite3.connect(self.db_path) as con:
            con.execute("""
                INSERT OR IGNORE INTO live_tokens_tracked
                (token_id, first_seen_ms, last_seen_ms, source, metadata_json)
                VALUES (?, ?, ?, ?, ?)
            """, (token_id, int(time.time()*1000), int(time.time()*1000), source, json.dumps(token)))
    
    def _save_snapshot(self, snap: LiveTokenSnapshot):
        """Guarda snapshot en ticks_crudos."""
        with sqlite3.connect(self.db_path) as con:
            con.execute("""
                INSERT OR REPLACE INTO ticks_crudos
                (token_id, ts_ms, price_usd, market_cap, liquidity, volume_24h,
                 change_m5, change_h1, buys_m5, sells_m5, holder_count,
                 net_buyers_1h, migration_progress, source, latency_ms, collected_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                snap.token_id, snap.ts_ms, snap.price_usd, snap.market_cap,
                snap.liquidity, snap.volume_24h, snap.change_m5, snap.change_h1,
                snap.buys_m5, snap.sells_m5, snap.holder_count, snap.net_buyers_1h,
                snap.migration_progress, snap.source, snap.latency_ms,
                int(time.time()*1000)
            ))
        
        # Actualizar last_seen
        with sqlite3.connect(self.db_path) as con:
            con.execute("""
                UPDATE live_tokens_tracked SET last_seen_ms = ?
                WHERE token_id = ?
            """, (snap.ts_ms, snap.token_id))
        
        self.stats["snapshots"] += 1
        self.stats["tokens_tracked"].add(snap.token_id)
    
    def stop(self):
        self.running = False
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            "snapshots_collected": self.stats["snapshots"],
            "errors": self.stats["errors"],
            "unique_tokens": len(self.stats["tokens_tracked"]),
            "sources": [s.get_name() for s in self.sources]
        }


class PlaywrightDexScreenerSource(DataSource):
    """
    Fuente DexScreener usando Playwright para scraping real.
    Útil cuando la API tiene rate limits o no expone todos los datos.
    """
    
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser = None
        self.context = None
        self.page = None
    
    def get_name(self) -> str:
        return "playwright_dexscreener"
    
    async def _init_browser(self):
        if self.browser is None:
            from playwright.async_api import async_playwright
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=self.headless)
            self.context = await self.browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            self.page = await self.context.new_page()
    
    async def fetch_new_tokens(self) -> List[Dict[str, Any]]:
        await self._init_browser()

        try:
            # Navegar a página de nuevos pares de Solana en DexScreener
            await self.page.goto("https://dexscreener.com/solana", wait_until="networkidle", timeout=30000)
        except Exception:
            return []

        # Esperar a que cargue la tabla (fail-closed si no carga)
        try:
            await self.page.wait_for_selector('[data-testid="pair-row"]', timeout=10000)
        except Exception:
            return []

        # Extraer datos de la tabla
        tokens = await self.page.evaluate("""
            () => {
                const rows = document.querySelectorAll('[data-testid="pair-row"]');
                const tokens = [];
                rows.forEach(row => {
                    const link = row.querySelector('a[href*="/solana/"]');
                    if (link) {
                        const href = link.href;
                        const match = href.match(r/\/solana\/([A-Za-z0-9]+)/);
                        if (match) {
                            const nameEl = row.querySelector('[data-testid="pair-name"]');
                            const priceEl = row.querySelector('[data-testid="price"]');
                            const liqEl = row.querySelector('[data-testid="liquidity"]');
                            tokens.push({
                                token_id: match[1],
                                name: nameEl?.textContent?.trim() || "",
                                price: priceEl?.textContent?.trim() || "",
                                liquidity: liqEl?.textContent?.trim() || "",
                                source: "playwright_dexscreener"
                            });
                        }
                    }
                });
                return tokens;
            }
        """)

        return tokens
    
    async def fetch_token_snapshot(self, token_id: str) -> Optional[LiveTokenSnapshot]:
        await self._init_browser()

        try:
            await self.page.goto(f"https://dexscreener.com/solana/{token_id}", wait_until="networkidle", timeout=30000)
        except Exception:
            return None

        # Extraer datos de la página del token (fail-closed si no hay precio)
        try:
            data = await self.page.evaluate("""
                () => {
                    const price = document.querySelector('[data-testid="price"]')?.textContent?.trim();
                    const mc = document.querySelector('[data-testid="market-cap"]')?.textContent?.trim();
                    const liq = document.querySelector('[data-testid="liquidity"]')?.textContent?.trim();
                    const vol = document.querySelector('[data-testid="volume-24h"]')?.textContent?.trim();
                    const ch5 = document.querySelector('[data-testid="change-5m"]')?.textContent?.trim();
                    const ch1 = document.querySelector('[data-testid="change-1h"]')?.textContent?.trim();
                    const buys = document.querySelector('[data-testid="buys-5m"]')?.textContent?.trim();
                    const sells = document.querySelector('[data-testid="sells-5m"]')?.textContent?.trim();
                    
                    return {price, mc, liq, vol, ch5, ch1, buys, sells};
                }
            """, timeout=10000)
        except Exception:
            return None

        if not data or not data.get("price"):
            return None
        
        # Parsear valores numéricos
        def parse_num(s: str) -> float:
            if not s: return 0.0
            s = s.replace("$", "").replace(",", "").replace("%", "")
            mult = 1
            if "K" in s: mult = 1000
            elif "M" in s: mult = 1000000
            elif "B" in s: mult = 1000000000
            try:
                return float(s.replace("K", "").replace("M", "").replace("B", "")) * mult
            except:
                return 0.0
        
        return LiveTokenSnapshot(
            token_id=token_id,
            ts_ms=int(time.time()*1000),
            price_usd=parse_num(data["price"]),
            market_cap=parse_num(data["mc"]),
            liquidity=parse_num(data["liq"]),
            volume_24h=parse_num(data["vol"]),
            change_m5=parse_num(data["ch5"]),
            change_h1=parse_num(data["ch1"]),
            buys_m5=int(parse_num(data["buys"])),
            sells_m5=int(parse_num(data["sells"])),
            holder_count=None,
            net_buyers_1h=None,
            migration_progress=None,
            source="playwright_dexscreener",
            latency_ms=0
        )
    
    async def close(self):
        if self.browser:
            await self.browser.close()
            await self.playwright.stop()


async def main():
    """Demo del colector en vivo con fuentes reales."""
    db_path = os.environ.get("PUMPSCOPE_DB", "custom.db")

    collector = LiveCollector(db_path)

    # Fuente 1: DexScreener HTTP API real
    dex_source = DexScreenerSource()
    collector.add_source(dex_source)

    # Fuente 2: Playwright DexScreener (fallback/scraping)
    pw_source = PlaywrightDexScreenerSource(headless=True)
    collector.add_source(pw_source)

    try:
        # Ejecutar una iteración de prueba con DexScreener HTTP
        print("[LiveCollector] Probando DexScreener HTTP API...")
        await collector._collect_from_source(dex_source)
        print(f"Stats después de DexScreener: {collector.get_stats()}")

        # Probar Playwright (puede fallar si no hay navegador instalado)
        print("[LiveCollector] Probando Playwright DexScreener...")
        try:
            await collector._collect_from_source(pw_source)
            print(f"Stats después de Playwright: {collector.get_stats()}")
        except Exception as e:
            print(f"[LiveCollector] Playwright no disponible (fall-closed): {e}")

        print(f"\nStats finales: {collector.get_stats()}")

        # Mostrar ticks_crudos recolectados
        with sqlite3.connect(db_path) as con:
            count = con.execute("SELECT COUNT(*) FROM ticks_crudos").fetchone()[0]
            tokens = con.execute("SELECT COUNT(DISTINCT token_id) FROM live_tokens_tracked").fetchone()[0]
            print(f"\n[ticks_crudos] {count} snapshots, {tokens} tokens únicos")
    finally:
        await pw_source.close()


if __name__ == "__main__":
    asyncio.run(main())