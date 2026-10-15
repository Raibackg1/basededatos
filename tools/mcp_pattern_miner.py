#!/usr/bin/env python3
"""
Servidor MCP (Model Context Protocol) para Minería Cuantitativa y Scraping de Patrones DEX / Pump.fun
===================================================================================================

Implementa el protocolo estándar MCP (JSON-RPC 2.0 sobre stdin/stdout) para que cualquier
asistente de IA (Antigravity, Claude, etc.) o herramienta cliente pueda consultar:
1. 'audit_database_patterns': Ejecuta minería cuantitativa en custom.db (crypto-chart-patterns, pumpfun-momentum-bot).
2. 'evaluate_token_pattern': Evalúa un vector de precios/snapshots de un token e identifica patrones causales.
3. 'scrape_dexscreener_token': Scraper en vivo de DexScreener API para pares de Solana (liquidez, volumen, buy/sell ratio).
4. 'get_regime_statistics': Consulta las probabilidades de supervivencia empíricas por banda de market cap.

Compatible 100% con Windows nativo (Python puro, sin dependencias C++ complejas).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import urllib.request
import urllib.error

import numpy as np
from scipy.signal import argrelextrema

# Configurar logging hacia stderr para no contaminar stdout (que transporta JSON-RPC)
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="[MCP-PatternMiner] %(message)s")

DB_PATH = Path(r"D:\hjh\pumpscope\db\custom.db")


def rpc_response(req_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def rpc_error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


class MCPPatternMinerServer:
    """Servidor MCP JSON-RPC 2.0."""

    SERVER_INFO = {
        "name": "pumpfun-pattern-miner-mcp",
        "version": "1.0.0",
    }

    TOOLS = [
        {
            "name": "audit_database_patterns",
            "description": "Ejecuta minería cuantitativa causal en custom.db usando algoritmos de crypto-chart-patterns y pumpfun-momentum-bot.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "max_tokens": {"type": "integer", "description": "Máximo número de tokens a escanear (default 500)", "default": 500},
                    "sl_pct": {"type": "number", "description": "Stop Loss real en % (default -33.49)", "default": -33.49},
                    "tp_pct": {"type": "number", "description": "Take Profit en % (default 50.0)", "default": 50.0},
                },
            },
        },
        {
            "name": "evaluate_token_pattern",
            "description": "Evalúa un token en base a una serie de precios observada, detectando patrones de extremos y momentum causal.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Símbolo del token"},
                    "prices": {"type": "array", "items": {"type": "number"}, "description": "Serie de precios o market caps cronológicos"},
                },
                "required": ["symbol", "prices"],
            },
        },
        {
            "name": "scrape_dexscreener_token",
            "description": "Scrapea métricas en vivo (precio, liquidez, volumen 5m/1h, txs) de cualquier token de Solana vía DexScreener API.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mint_address": {"type": "string", "description": "Dirección mint del token en Solana"},
                },
                "required": ["mint_address"],
            },
        },
        {
            "name": "get_regime_statistics",
            "description": "Obtiene la tasa empírica de supervivencia (+50% / +100%) por banda de Market Cap basada en 52,000+ tokens.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
    ]

    def handle_initialize(self, req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        return rpc_response(
            req_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": self.SERVER_INFO,
            },
        )

    def handle_tools_list(self, req_id: Any) -> Dict[str, Any]:
        return rpc_response(req_id, {"tools": self.TOOLS})

    def handle_tool_call(self, req_id: Any, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if name == "audit_database_patterns":
                return self._tool_audit_db(req_id, args)
            elif name == "evaluate_token_pattern":
                return self._tool_evaluate_token(req_id, args)
            elif name == "scrape_dexscreener_token":
                return self._tool_scrape_dexscreener(req_id, args)
            elif name == "get_regime_statistics":
                return self._tool_get_regimes(req_id, args)
            else:
                return rpc_error(req_id, -32601, f"Herramienta no encontrada: {name}")
        except Exception as e:
            logging.error(f"Error ejecutando herramienta {name}: {e}")
            return rpc_response(
                req_id,
                {
                    "content": [{"type": "text", "text": f"Error ejecutando {name}: {str(e)}"}],
                    "isError": True,
                },
            )

    def _tool_audit_db(self, req_id: Any, args: Dict[str, Any]) -> Dict[str, Any]:
        from db_opensource_miner import OpenSourcePatternMiner
        max_tokens = int(args.get("max_tokens", 500))
        miner = OpenSourcePatternMiner(db_path=DB_PATH)
        miner.SL_REAL_PCT = float(args.get("sl_pct", -33.49))
        miner.TP_NOMINAL_PCT = float(args.get("tp_pct", 50.0))

        chart_pats = miner.mine_chart_extrema_patterns(max_tokens=max_tokens)
        momentum_pat = miner.mine_pumpfun_momentum_patterns(max_tokens=max_tokens)

        text_result = json.dumps(
            {
                "status": "success",
                "max_tokens_scanned": max_tokens,
                "chart_patterns": {k: v.__dict__ for k, v in chart_pats.items()},
                "momentum_pattern": momentum_pat.__dict__,
            },
            indent=2,
        )
        return rpc_response(req_id, {"content": [{"type": "text", "text": text_result}]})

    def _tool_evaluate_token(self, req_id: Any, args: Dict[str, Any]) -> Dict[str, Any]:
        symbol = str(args.get("symbol", "UNKNOWN"))
        prices = np.array(args.get("prices", []), dtype=float)

        if len(prices) < 5:
            res = {"symbol": symbol, "pattern": "INSUFFICIENT_DATA", "verdict": "VETO", "reason": "Menos de 5 puntos"}
            return rpc_response(req_id, {"content": [{"type": "text", "text": json.dumps(res, indent=2)}]})

        # Evaluar extremos
        local_min = argrelextrema(prices, np.less, order=1)[0]
        local_max = argrelextrema(prices, np.greater, order=1)[0]

        detected = "CONSOLIDATION"
        bias = "NEUTRAL"
        confidence = 50.0

        if len(local_min) >= 2 and len(local_max) >= 1:
            m1, m2 = local_min[-2], local_min[-1]
            diff = abs(prices[m1] - prices[m2]) / max(prices[m1], 1e-9)
            if diff <= 0.08 and prices[-1] > prices[m2]:
                detected = "DOUBLE_BOTTOM"
                bias = "BULLISH"
                confidence = 72.0

        gain_pct = ((prices[-1] - prices[0]) / prices[0]) * 100.0 if prices[0] > 0 else 0.0
        if gain_pct >= 20.0 and detected != "DOUBLE_BOTTOM":
            detected = "MOMENTUM_EXPANSION"
            bias = "BULLISH"
            confidence = 65.0

        result = {
            "symbol": symbol,
            "detected_pattern": detected,
            "bias": bias,
            "confidence_pct": confidence,
            "price_gain_pct": round(gain_pct, 2),
            "recommendation": "BUY_CONFIRM" if bias == "BULLISH" and confidence >= 70.0 else "WAIT_OR_VETO",
        }
        return rpc_response(req_id, {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]})

    def _tool_scrape_dexscreener(self, req_id: Any, args: Dict[str, Any]) -> Dict[str, Any]:
        mint = str(args.get("mint_address", "")).strip()
        if not mint:
            return rpc_response(req_id, {"content": [{"type": "text", "text": "Error: mint_address vacío"}], "isError": True})

        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        req = urllib.request.Request(url, headers={"User-Agent": "Antigravity-PatternMiner/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            pairs = data.get("pairs") or []
            if not pairs:
                res = {"mint": mint, "status": "NOT_FOUND", "message": "Sin pares encontrados en DexScreener"}
            else:
                p = pairs[0]
                res = {
                    "mint": mint,
                    "symbol": p.get("baseToken", {}).get("symbol", ""),
                    "dexId": p.get("dexId", ""),
                    "priceUsd": float(p.get("priceUsd") or 0.0),
                    "liquidityUsd": float(p.get("liquidity", {}).get("usd") or 0.0),
                    "marketCapUsd": float(p.get("marketCap") or 0.0),
                    "volume5m": float(p.get("volume", {}).get("m5") or 0.0),
                    "txs5m": p.get("txns", {}).get("m5", {}),
                    "priceChange5m": float(p.get("priceChange", {}).get("m5") or 0.0),
                }
            return rpc_response(req_id, {"content": [{"type": "text", "text": json.dumps(res, indent=2)}]})
        except Exception as ex:
            return rpc_response(
                req_id,
                {"content": [{"type": "text", "text": f"Error conectando a DexScreener: {str(ex)}"}], "isError": True},
            )

    def _tool_get_regimes(self, req_id: Any, args: Dict[str, Any]) -> Dict[str, Any]:
        report_path = Path(r"D:\mejoradordebase de datos\outputs\informe_patrones_opensource_custom_db.json")
        if report_path.exists():
            with open(report_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            regimes = data.get("market_cap_regimes", {})
        else:
            regimes = {"info": "Ejecute audit_database_patterns primero"}
        return rpc_response(req_id, {"content": [{"type": "text", "text": json.dumps(regimes, indent=2)}]})

    def run_stdio(self):
        """Loop de ejecución estándar de servidor MCP por stdio."""
        logging.info("Servidor MCP iniciado y listo para recibir llamadas JSON-RPC...")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            req_id = msg.get("id")
            method = msg.get("method")
            params = msg.get("params", {})

            if method == "initialize":
                resp = self.handle_initialize(req_id, params)
            elif method == "tools/list":
                resp = self.handle_tools_list(req_id)
            elif method == "tools/call":
                resp = self.handle_tool_call(req_id, params.get("name", ""), params.get("arguments", {}))
            elif method == "notifications/initialized":
                continue  # Notificación unilateral, no responder
            else:
                resp = rpc_error(req_id, -32601, f"Método no soportado: {method}")

            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    server = MCPPatternMinerServer()
    server.run_stdio()
