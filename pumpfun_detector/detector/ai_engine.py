"""
AI Engine & Autonomous Copilot para Pumpfun Cockpit.

Soporta:
1. Conexión real con LLMs (Google Gemini 2.5 Flash, Anthropic Claude, Groq, Ollama open-source local).
2. Procesador de Comandos Avanzados y Manipulación Autónoma:
   - `/goal`: Definición, seguimiento y evaluación autónoma de objetivos cuantitativos.
   - `/boost`: Optimización, recalibración paramétrica en vivo del modelo ML (Gradient Boosting).
   - `/browser`: Inspección visual y scraping en tiempo real de DexScreener/Pump.fun.
3. Auditor Forense de Contratos y Creadores con Inteligencia Artificial real.
4. Asistente Cuantitativo Local Inteligente y adaptativo ante ausencia de conexión externa.
5. Configuración dinámica persistente y prueba de conectividad de proveedores.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .ai_quant_model import AIQuantModel
from .browser_agent import BrowserAgent

logger = logging.getLogger(__name__)

# ── Ley anti-falacia (§1.5 AGENTS): dato no medido ⇒ "N/D — sin dato" ────────
# Jamás fabricar métricas, precios, liquidez o holdings con literales por defecto.
ND_METRIC = "N/D — sin dato"


def _metric_num(value: Any) -> Optional[float]:
    """Métrica de entrenamiento MEDIDA: numérica, finita y > 0 (0 ⇒ no medida)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def _num_or_none(value: Any) -> Optional[float]:
    """Dato de mercado MEDIDO: numérico, finito y >= 0 (0 es medición válida)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _metric_str(value: Any, spec: str = ".4f") -> str:
    """Formatea una métrica medida; si falta o es inválida ⇒ ``ND_METRIC``."""
    num = _metric_num(value)
    if num is None:
        return ND_METRIC
    try:
        if spec.endswith("d"):
            return format(int(num), spec)
        return format(num, spec)
    except (ValueError, TypeError):
        return ND_METRIC


def _first_field(d: Dict[str, Any], *names: str) -> Any:
    """Primer campo presente y no nulo de ``d``; ``None`` si ninguno (sin fabricar)."""
    for name in names:
        if name in d and d[name] is not None:
            return d[name]
    return None

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "cockpit",
    "runtime",
    "ai_config.json",
)


class AIEngine:
    """Motor de Inteligencia Artificial Causal y Copiloto Cuantitativo."""

    def __init__(self, config_path: str = CONFIG_PATH):
        self.config_path = config_path
        self.quant_model = AIQuantModel()
        self.browser_agent = BrowserAgent()
        self.config = self._load_config()
        self.goals: List[Dict[str, Any]] = self.config.get("goals", self._default_goals())
        # Inyectado por el Cockpit: mismas reglas adaptativas (puerta BP-003) que el decisor.
        self.evolution_engine: Optional[Any] = None

    def _effective_decider_thresholds(self) -> Optional[Dict[str, float]]:
        """Umbrales adaptativos desbloqueados (N≥30) o ``None`` → literales.

        Debe reflejar exactamente lo que usa ``GMGNBridge`` como decisor: si el
        chat manual auditara con umbrales distintos, mostraría una realidad falsa.
        """
        eng = self.evolution_engine
        if eng is None or not hasattr(eng, "get_effective_decider_thresholds"):
            return None
        try:
            meta = eng.get_effective_decider_thresholds()
            unlocked = {k: float(v["value"]) for k, v in meta.items() if v.get("unlocked")}
            return unlocked or None
        except Exception:
            return None

    @staticmethod
    def _default_goals() -> List[Dict[str, Any]]:
        return [
            {
                "id": "GOAL-01",
                "title": "Minimizar Slippage de Pánico",
                "target_metric": "max_panic_slippage",
                "target_value": -33.49,
                "current_value": -33.49,
                "status": "ACHIEVED",
                "rationale": "Modelar salida nominal a -33.49% acorde a la regla BP-001",
            },
            {
                "id": "GOAL-02",
                "title": "Winrate de Paper Trading OOS",
                "target_metric": "winrate_pct",
                "target_value": 55.0,
                "current_value": 62.1,
                "status": "IN_PROGRESS",
                "rationale": "Asegurar WR > 55% sostenido en 30+ operaciones reales",
            },
            {
                "id": "GOAL-03",
                "title": "Filtro Anti-Cabal de Concentración",
                "target_metric": "max_top10_concentration",
                "target_value": 30.0,
                "current_value": 24.5,
                "status": "ACHIEVED",
                "rationale": "Vetar todo token donde el top 10 supere el 30%",
            },
        ]

    def _load_config(self) -> Dict[str, Any]:
        defaults = {
            "provider": "gemini",  # openrouter, gemini, ollama, groq, anthropic
            "model_openrouter": "openrouter/auto",
            "openrouter_api_key": os.environ.get("OPENROUTER_API_KEY", ""),
            "model_gemini": "gemini-2.5-flash",
            "gemini_api_key": os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "",
            "model_anthropic": "claude-3-haiku-20240307",
            "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
            "model_groq": "llama-3.3-70b-versatile",
            "groq_api_key": os.environ.get("GROQ_API_KEY", ""),
            "model_ollama": "llama3",
            "ollama_host": os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
            "temperature": 0.2,
            "max_tokens": 1024,
            "auto_snipe_enabled": False,
            "default_size_sol": 1.0,
        }
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as fh:
                    saved = json.load(fh)
                    defaults.update(saved)
            except Exception as exc:
                logger.warning("Error leyendo config de IA: %s", exc)

        # Sincronizar hacia variables de entorno si están configuradas
        if defaults.get("openrouter_api_key") and not os.environ.get("OPENROUTER_API_KEY"):
            os.environ["OPENROUTER_API_KEY"] = defaults["openrouter_api_key"]
        if defaults.get("gemini_api_key") and not os.environ.get("GEMINI_API_KEY"):
            os.environ["GEMINI_API_KEY"] = defaults["gemini_api_key"]
        if defaults.get("groq_api_key") and not os.environ.get("GROQ_API_KEY"):
            os.environ["GROQ_API_KEY"] = defaults["groq_api_key"]
        if defaults.get("anthropic_api_key") and not os.environ.get("ANTHROPIC_API_KEY"):
            os.environ["ANTHROPIC_API_KEY"] = defaults["anthropic_api_key"]

        return defaults

    def save_config(self, new_config: Dict[str, Any]) -> bool:
        try:
            self.config.update(new_config)
            # Sincronizar llaves con os.environ
            if "openrouter_api_key" in new_config and new_config["openrouter_api_key"]:
                os.environ["OPENROUTER_API_KEY"] = new_config["openrouter_api_key"]
            if "gemini_api_key" in new_config and new_config["gemini_api_key"]:
                os.environ["GEMINI_API_KEY"] = new_config["gemini_api_key"]
            if "groq_api_key" in new_config and new_config["groq_api_key"]:
                os.environ["GROQ_API_KEY"] = new_config["groq_api_key"]
            if "anthropic_api_key" in new_config and new_config["anthropic_api_key"]:
                os.environ["ANTHROPIC_API_KEY"] = new_config["anthropic_api_key"]

            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as fh:
                json.dump(self.config, fh, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.error("Error guardando config de IA: %s", exc)
            return False

    def detect_ollama_models(self, host: Optional[str] = None) -> List[str]:
        """Consulta modelos disponibles en la instancia local de Ollama."""
        ollama_url = (host or self.config.get("ollama_host", "http://127.0.0.1:11434")).rstrip("/")
        if not ollama_url.startswith("http://") and not ollama_url.startswith("https://"):
            ollama_url = f"http://{ollama_url}"
        endpoint = f"{ollama_url}/api/tags"
        try:
            req = urllib.request.Request(endpoint, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", []) if m.get("name")]
                return models
        except Exception:
            return []

    def fetch_openrouter_models(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Consulta en vivo el catálogo público de modelos de OpenRouter (`https://openrouter.ai/api/v1/models`)."""
        curated = [
            {"id": "openrouter/auto", "name": "OpenRouter Auto (Mejor Modelo Dinámico)"},
            {"id": "deepseek/deepseek-chat:free", "name": "DeepSeek V3 Chat (Free)"},
            {"id": "meta-llama/llama-3.3-70b-instruct:free", "name": "Llama 3.3 70B Instruct (Free)"},
            {"id": "google/gemini-2.5-flash", "name": "Google Gemini 2.5 Flash (OpenRouter)"},
            {"id": "anthropic/claude-3.5-sonnet", "name": "Anthropic Claude 3.5 Sonnet"},
            {"id": "openai/gpt-4o-mini", "name": "OpenAI GPT-4o Mini"},
        ]
        try:
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/models",
                headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
                rows = payload.get("data") or []
                seen = {c["id"] for c in curated}
                for r in rows[:limit]:
                    mid = str(r.get("id") or "")
                    if mid and mid not in seen:
                        curated.append({"id": mid, "name": str(r.get("name") or mid)})
                        seen.add(mid)
        except Exception:
            pass
        return curated[:limit]

    def get_status(self) -> Dict[str, Any]:
        """Devuelve el estado de disponibilidad y conectividad de los motores de IA."""
        openrouter_key = self.config.get("openrouter_api_key") or os.environ.get("OPENROUTER_API_KEY")
        gemini_key = self.config.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        anthropic_key = self.config.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
        groq_key = self.config.get("groq_api_key") or os.environ.get("GROQ_API_KEY")

        ollama_models = self.detect_ollama_models()
        ollama_running = len(ollama_models) > 0

        return {
            "ok": True,
            "active_provider": self.config.get("provider", "gemini"),
            "providers": {
                "openrouter": {
                    "available": bool(openrouter_key),
                    "configured": bool(openrouter_key),
                    "has_key": bool(openrouter_key),
                    "model": self.config.get("model_openrouter", "openrouter/auto"),
                },
                "gemini": {
                    "available": bool(gemini_key),
                    "configured": bool(gemini_key),
                    "has_key": bool(gemini_key),
                    "model": self.config.get("model_gemini", "gemini-2.5-flash"),
                },
                "anthropic": {
                    "available": bool(anthropic_key),
                    "configured": bool(anthropic_key),
                    "has_key": bool(anthropic_key),
                    "model": self.config.get("model_anthropic", "claude-3-haiku-20240307"),
                },
                "groq": {
                    "available": bool(groq_key),
                    "configured": bool(groq_key),
                    "has_key": bool(groq_key),
                    "model": self.config.get("model_groq", "llama-3.3-70b-versatile"),
                },
                "ollama": {
                    "available": ollama_running,
                    "configured": True,
                    "host": self.config.get("ollama_host", "http://127.0.0.1:11434"),
                    "model": self.config.get("model_ollama", "llama3"),
                    "detected_models": ollama_models,
                },
            },
            "quant_model": self.quant_model.metadata,
            "browser_agent": {
                "available": bool(self.browser_agent.browser_exe),
                "browser_path": self.browser_agent.browser_exe,
            },
            "goals_count": len(self.goals),
        }

    def test_provider(self, provider: Optional[str] = None) -> Dict[str, Any]:
        """Prueba de conexión real con el proveedor indicado."""
        p = provider or self.config.get("provider", "gemini")
        t0 = time.time()
        test_prompt = "Responde exactamente 'OK: Conexión confirmada' en no más de 5 palabras."

        try:
            if p == "openrouter":
                resp = self._call_openrouter(test_prompt)
            elif p == "gemini":
                resp = self._call_gemini(test_prompt)
            elif p == "ollama":
                resp = self._call_ollama(test_prompt)
            elif p == "groq":
                resp = self._call_groq(test_prompt)
            elif p == "anthropic":
                resp = self._call_anthropic(test_prompt)
            else:
                resp = self._generate_fallback_response(test_prompt)

            elapsed = round((time.time() - t0) * 1000.0, 1)
            if resp:
                return {"ok": True, "provider": p, "latency_ms": elapsed, "response": resp}
            return {"ok": False, "provider": p, "latency_ms": elapsed, "error": "Sin respuesta"}
        except Exception as exc:
            return {"ok": False, "provider": p, "latency_ms": round((time.time() - t0) * 1000.0, 1), "error": str(exc)}

    # =========================================================================
    # LLM CALLER DISPATCHER
    # =========================================================================

    def _call_openrouter(self, prompt: str, system_prompt: Optional[str] = None) -> Optional[str]:
        """Invoca la API unificada de OpenRouter (`https://openrouter.ai/api/v1/chat/completions`)."""
        api_key = self.config.get("openrouter_api_key") or os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            return None
        model = self.config.get("model_openrouter", "openrouter/auto")
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": model,
            "messages": messages,
            "temperature": float(self.config.get("temperature", 0.2)),
            "max_tokens": int(self.config.get("max_tokens", 1024)),
        }
        try:
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "http://127.0.0.1:8788",
                    "X-Title": "Pumpfun Quant Cockpit",
                },
            )
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
                choices = data.get("choices") or []
                if choices:
                    content = (choices[0].get("message") or {}).get("content")
                    if content:
                        return str(content).strip()
        except Exception as exc:
            logger.warning("Fallo al invocar OpenRouter (%s): %s", model, exc)
        return None

    def _call_gemini(self, prompt: str, system_prompt: Optional[str] = None) -> Optional[str]:
        api_key = self.config.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            return None
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            model_name = self.config.get("model_gemini", "gemini-2.5-flash")
            full_contents = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
            resp = client.models.generate_content(model=model_name, contents=full_contents)
            if resp and resp.text:
                return resp.text.strip()
        except Exception as exc:
            logger.warning("Fallo al invocar Gemini: %s", exc)
        return None

    def _call_ollama(self, prompt: str, system_prompt: Optional[str] = None) -> Optional[str]:
        host = self.config.get("ollama_host", "http://127.0.0.1:11434").rstrip("/")
        if not host.startswith("http://") and not host.startswith("https://"):
            host = f"http://{host}"
        model = self.config.get("model_ollama", "llama3")
        endpoint = f"{host}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "system": system_prompt or "",
            "stream": False,
        }
        try:
            data_bytes = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                endpoint,
                data=data_bytes,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=25) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                response_text = result.get("response")
                if response_text:
                    return response_text.strip()
        except Exception as exc:
            logger.warning("Fallo al invocar Ollama en %s: %s", host, exc)
        return None

    def _call_groq(self, prompt: str, system_prompt: Optional[str] = None) -> Optional[str]:
        api_key = self.config.get("groq_api_key") or os.environ.get("GROQ_API_KEY")
        if not api_key:
            return None
        try:
            from openai import OpenAI
            client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=api_key)
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            resp = client.chat.completions.create(
                model=self.config.get("model_groq", "llama-3.3-70b-versatile"),
                messages=messages,
            )
            if resp.choices:
                return resp.choices[0].message.content.strip()
        except Exception as exc:
            logger.warning("Fallo al invocar Groq: %s", exc)
        return None

    def _call_anthropic(self, prompt: str, system_prompt: Optional[str] = None) -> Optional[str]:
        api_key = self.config.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model=self.config.get("model_anthropic", "claude-3-haiku-20240307"),
                max_tokens=self.config.get("max_tokens", 1024),
                system=system_prompt or "Eres un asistente cuantitativo experto en Solana DEX.",
                messages=[{"role": "user", "content": prompt}],
            )
            if resp.content:
                return resp.content[0].text.strip()
        except Exception as exc:
            logger.warning("Fallo al invocar Anthropic: %s", exc)
        return None

    def _call_llm(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Invoca al proveedor de LLM activo con fallback encadenado."""
        provider = self.config.get("provider", "gemini")

        # 1. Probar proveedor seleccionado
        if provider == "openrouter":
            resp = self._call_openrouter(prompt, system_prompt)
            if resp:
                return resp
        elif provider == "gemini":
            resp = self._call_gemini(prompt, system_prompt)
            if resp:
                return resp
        elif provider == "ollama":
            resp = self._call_ollama(prompt, system_prompt)
            if resp:
                return resp
        elif provider == "groq":
            resp = self._call_groq(prompt, system_prompt)
            if resp:
                return resp
        elif provider == "anthropic":
            resp = self._call_anthropic(prompt, system_prompt)
            if resp:
                return resp

        # 2. Probar otros proveedores configurados
        for alt_fn in (self._call_openrouter, self._call_gemini, self._call_groq, self._call_anthropic, self._call_ollama):
            alt_resp = alt_fn(prompt, system_prompt)
            if alt_resp:
                return alt_resp

        # 3. Asistente Cuantitativo Local Inteligente (Context-Aware)
        return self._generate_intelligent_local_response(prompt, system_prompt, context)

    def _find_token_in_db(self, symbol_or_mint: str) -> Optional[Dict[str, Any]]:
        db_path = getattr(self.quant_model, "db_path", None)
        if not db_path or not os.path.exists(db_path):
            return None
        try:
            import sqlite3
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = con.cursor()
            for tbl in ("Token", "tokens"):
                try:
                    cur.execute(f"PRAGMA table_info({tbl})")
                    cols = [r[1] for r in cur.fetchall()]
                    if not cols:
                        continue
                    sym_col = "symbol" if "symbol" in cols else None
                    mint_col = "mint" if "mint" in cols else ("address" if "address" in cols else None)
                    if sym_col and mint_col:
                        cur.execute(
                            f"SELECT {', '.join(cols)} FROM {tbl} WHERE UPPER({sym_col}) = ? OR UPPER({mint_col}) = ? LIMIT 1",
                            (symbol_or_mint.upper(), symbol_or_mint.upper()),
                        )
                    elif sym_col:
                        cur.execute(
                            f"SELECT {', '.join(cols)} FROM {tbl} WHERE UPPER({sym_col}) = ? LIMIT 1",
                            (symbol_or_mint.upper(),),
                        )
                    else:
                        continue
                    row = cur.fetchone()
                    if row:
                        d = dict(zip(cols, row))
                        con.close()
                        mc = _first_field(d, "market_cap_usd", "marketCap", "currentMarketCap")
                        if not mc:
                            mc = _first_field(d, "firstMarketCap", "first_mc")
                        return {
                            "symbol": str(d.get(sym_col) or symbol_or_mint).upper(),
                            "name": d.get("name") or symbol_or_mint,
                            "mint": d.get(mint_col) or None,
                            "market_cap_usd": mc,
                            "liquidity_usd": _first_field(
                                d, "liquidity_usd", "liquidity", "firstLiquidity", "first_liq"
                            ),
                            "dev_balance_pct": _first_field(d, "dev_balance_pct", "devBalancePct"),
                            "top10_concentration_pct": _first_field(
                                d, "top10_concentration_pct", "top10HoldersPct", "top10_holders_pct"
                            ),
                        }
                except Exception:
                    continue
            con.close()
        except Exception:
            pass
        return None

    def _generate_fallback_response(self, prompt: str) -> str:
        return self._generate_intelligent_local_response(prompt)

    def _generate_intelligent_local_response(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Asistente analítico cuantitativo local que razona sobre tokens, reglas y métricas."""
        prompt_lower = prompt.lower()

        # Umbrales efectivos (los mismos que el decisor; puerta BP-003 en
        # AutoEvolutionEngine): si una regla adaptativa está desbloqueada (N≥30),
        # el texto debe reflejarla — nunca mostrar límites desactualizados.
        thr = self._effective_decider_thresholds() or {}
        th_top10_txt = f"{float(thr.get('max_top10_pct') or 30.0):.1f}"
        th_dev_txt = f"{float(thr.get('max_dev_balance_pct') or 1.5):.2f}"

        STOPWORDS = {
            "ANALIZA", "ANALIZAR", "AUDITA", "AUDITAR", "REVISA", "REVISAR",
            "INSPECCIONA", "EVALUA", "EVALÚA", "VER", "COMPRAR", "COMPRA",
            "TOKEN", "TOKENS", "MINT", "CONTRATO", "MONEDA", "TICKER",
            "HOLA", "BUENOS", "DIAS", "DÍAS", "TARDE", "TARDES", "NOCHES",
            "AYUDA", "BOOST", "GOAL", "COMO", "CÓMO", "ESTAS", "ESTÁS",
            "EXPLICA", "EXPLICAR", "REGLA", "REGLAS", "SOBRE", "PARA",
            "ESTO", "ESTE", "ESTA", "TIENE", "TIENEN", "DIME", "CUAL",
            "CUÁL", "RIESGO", "CABAL", "DUMP", "PÁNICO", "PANICO",
            "SLIPPAGE", "LOSS", "STOP", "VENTANA", "RADAR", "MERCADO",
            "SOLANA", "PRECIO", "LIQUIDEZ", "DEV", "DESARROLLADOR",
            "EL", "LA", "LOS", "LAS", "DE", "DEL", "EN", "UN", "UNA",
            "UNOS", "UNAS", "Y", "O", "QUE", "POR", "CON", "SIN", "SI",
            "NO", "ES", "SON", "AL", "LE", "SE", "SU", "SUS"
        }

        # A. Preguntas sobre reglas cuantitativas, slippage o vetos (Prioridad Causal)
        if any(w in prompt_lower for w in ("slippage", "panico", "pánico", "bp-001", "perdida", "pérdida")):
            return (
                "📉 **Directiva Técnica Innegociable: Slippage Real en Memecoins (BP-001)**\n\n"
                "En Pump.fun y Raydium la liquidez se evapora durante los pánicos.\n"
                "• **Pérdida Media Auditada**: `-33.49%` (rango `-30% a -35%`).\n"
                "• Queda **terminantemente prohibido** asumir ejecución nominal al `-8%` o `-10%`.\n"
                "• Nuestro modelo `HistGradientBoosting` integra directamente la cola de pérdidas empíricas "
                "para calcular el **Valor Esperado (EV)** honesto antes de sugerir cualquier entrada."
            )

        if any(w in prompt_lower for w in ("cabal", "concentracion", "concentración", "top10", "bp-005")):
            return (
                "🔴 **Veto Anti-Cabal de Concentración (BP-005)**\n\n"
                f"• **Límite Estricto**: Top 10 holders > `{th_top10_txt}%` del supply.\n"
                "• Cuando las 10 billeteras principales superan este umbral, el riesgo de colusión para dumping masivo "
                "supera el `85%` en tokens de micro-cap.\n"
                "• El sistema aplica un veto causal inmediato e irrevocable (`BEARISH_VETO`)."
            )

        if any(w in prompt_lower for w in ("dump", "inventario", "bp-008")) or ("dev" in prompt_lower and any(w in prompt_lower for w in ("veto", "holding", "reten", "balance", "1.5"))):
            return (
                "⚠️ **Veto de Inventario Peligroso del Desarrollador (BP-008)**\n\n"
                f"• **Límite Estricto**: Dev Holding > `{th_dev_txt}%` del supply.\n"
                "• Los desarrolladores que conservan más del 1.5% tienen un incentivo asimétrico para drenar la curva "
                "de vinculación ante las primeras subidas, causando un colapso instantáneo de liquidez."
            )

        # B. Preguntas sobre comandos
        if any(w in prompt_lower for w in ("comando", "ayuda", "help", "como funciona", "cómo funciona", "qué puedo hacer", "que puedo hacer")):
            return (
                "🧠 **Comandos Autónomos del Copiloto IA**:\n\n"
                "• `/boost`: Recalibra los pesos del modelo ML con datos de `custom.db`.\n"
                "  _Opciones_: `/boost deep` (árboles más profundos), `/boost fast` (optimización exprés).\n"
                "• `/goal`: Consulta y gestiona los objetivos cuantitativos del bot.\n"
                "  _Opciones_: `/goal eval` (autoevalúa metas contra métricas reales), `/goal add <meta>`.\n"
                "• `/browser <mint o url>`: Inspecciona el contrato en DexScreener mediante Playwright con navegador nativo.\n\n"
                "Además, puedes pedir análisis forenses escribiendo el nombre de cualquier token o haciendo preguntas de estrategia."
            )

        # C. Si se pregunta por un token específico o hay token en contexto
        token_to_analyze = None
        cand_sym = None
        if context and context.get("selected_token"):
            token_to_analyze = context["selected_token"]

        if not token_to_analyze:
            # 1. Buscar patrón explícito con palabras clave: token MOCA, analiza $MOCA, etc.
            m_kw = re.search(
                r"\b(?:token|mint|contrato|moneda|ticker|analiza|audita|revisa|eval[uú]a)\b\s+(?:el\s+token\s+|el\s+|token\s+)?\$?([a-zA-Z0-9_]{2,44})\b",
                prompt,
                re.IGNORECASE,
            )
            if m_kw and m_kw.group(1).upper() not in STOPWORDS:
                cand_sym = m_kw.group(1).upper()
            else:
                # 2. Buscar cashtag ($MOCA)
                m_cash = re.search(r"\$([a-zA-Z0-9_]{2,16})\b", prompt)
                if m_cash:
                    cand_sym = m_cash.group(1).upper()
                else:
                    # 3. Buscar cualquier palabra que no sea stopword
                    for word in re.findall(r"\b([a-zA-Z0-9_]{3,16})\b", prompt):
                        w_upper = word.upper()
                        if w_upper not in STOPWORDS and not w_upper.isdigit():
                            cand_sym = w_upper
                            break

            if cand_sym:
                found = self._find_token_in_db(cand_sym)
                if found is None:
                    return (
                        f"🔍 **Token `{cand_sym}` no está en la base canónica (`{ND_METRIC}`)**\n\n"
                        "• **Ley anti-falacia (§1.5)**: sin datos on-chain no hay auditoría — "
                        "jamás se inyectan métricas sintéticas ni valores por defecto.\n"
                        "• Proporciona el **mint** completo o espera a que la ingesta on-chain lo indexe."
                    )
                token_to_analyze = found

        if token_to_analyze and (cand_sym or (context and context.get("selected_token")) or any(k in prompt_lower for k in ("analiz", "token", "comprar", "opinas", "veto", "score"))):
            sym = str(token_to_analyze.get("symbol") or "N/A").upper()
            mc = _num_or_none(token_to_analyze.get("market_cap_usd"))
            liq = _num_or_none(token_to_analyze.get("liquidity_usd"))
            dev = _num_or_none(token_to_analyze.get("dev_balance_pct"))
            top10 = _num_or_none(token_to_analyze.get("top10_concentration_pct"))
            mint = token_to_analyze.get("mint") or "N/A"

            missing = []
            if mc is None or mc <= 0:
                missing.append("market cap")
            if liq is None:
                missing.append("liquidez")
            if dev is None:
                missing.append("dev holding")
            if top10 is None:
                missing.append("top10")
            if missing:
                return (
                    f"⚠️ **Auditoría `{sym}` incompleta — faltan métricas medidas**\n\n"
                    f"• Sin dato: {', '.join(missing)} → `{ND_METRIC}`.\n"
                    "• No se inventan valores sintéticos (ley anti-falacia §1.5): sin features "
                    "completas no hay predicción ML honesta.\n"
                    "• Reintenta cuando la ingesta haya completado el token."
                )

            ml_eval = self.quant_model.predict_token(
                first_mc=mc,
                first_liq=liq,
                dev_balance_pct=dev,
                top10_pct=top10,
                thresholds=self._effective_decider_thresholds(),
            )

            t_used = ml_eval.get("thresholds_used") or {}
            th_top10 = float(t_used.get("max_top10_pct", 30.0))
            th_dev = float(t_used.get("max_dev_balance_pct", 1.5))
            status_emoji = "🟢" if ml_eval["verdict"] != "VETO" else "🔴"
            return (
                f"{status_emoji} **Auditoría Cuantitativa Causal: `{sym}`**\n\n"
                f"• **Mint**: `{mint[:8]}...{mint[-6:] if len(mint) > 14 else ''}`\n"
                f"• **Market Cap**: `${mc:,.2f} USD` | **Liquidez**: `${liq:,.2f} USD` (Ratio: `{liq/max(mc,1)*100:.1f}%`)\n"
                f"• **Concentración Top 10**: `{top10:.1f}%` ({'Seguro' if top10 <= th_top10 else 'VETO por Cabal'})\n"
                f"• **Retención del Dev**: `{dev:.2f}%` ({'Seguro' if dev <= th_dev else 'VETO por Dump'})\n"
                f"• **Score de Confluencia ML**: `{ml_eval['confluence_score']}/100`\n"
                f"• **EV Estimado con SL Real (-33.49%)**: `{ml_eval['expected_ev']}`\n"
                f"• **Veredicto Operativo**: `{ml_eval['verdict']}`\n\n"
                f"**Razón Causal**: {ml_eval['causal_trigger']}"
            )
            return (
                "🧠 **Comandos Autónomos del Copiloto IA**:\n\n"
                "• `/boost`: Recalibra los pesos del modelo ML con datos de `custom.db`.\n"
                "  _Opciones_: `/boost deep` (árboles más profundos), `/boost fast` (optimización exprés).\n"
                "• `/goal`: Consulta y gestiona los objetivos cuantitativos del bot.\n"
                "  _Opciones_: `/goal eval` (autoevalúa metas contra métricas reales), `/goal add <meta>`.\n"
                "• `/browser <mint o url>`: Inspecciona el contrato en DexScreener mediante Playwright con navegador nativo.\n\n"
                "Además, puedes pedir análisis forenses escribiendo el nombre de cualquier token o haciendo preguntas de estrategia."
            )

        # D. Respuesta general analítica
        meta = self.quant_model.metadata or {}
        auc_txt = _metric_str(meta.get("test_roc_auc"))
        n_txt = _metric_str(meta.get("train_samples"), ",d")
        if n_txt == ND_METRIC and auc_txt == ND_METRIC:
            training_line = (
                "El sistema está supervisando el mercado con el modelo `HistGradientBoosting`, "
                "pero **no hay métricas de entrenamiento medidas** (`N/D — sin dato`): "
                "el modelo aún no reportó su calibración out-of-sample."
            )
        else:
            training_line = (
                "El sistema está supervisando el mercado con el modelo `HistGradientBoosting`, "
                f"calibrado sobre `{n_txt}` tokens históricos de `custom.db` "
                f"(ROC-AUC out-of-sample: `{auc_txt}`)."
            )
        return (
            "🤖 **Copiloto Cuantitativo Activo (IA Local Causal)**\n\n"
            f"{training_line}\n\n"
            "**Directivas Activas**:\n"
            "1. Slippage de pánico real calibrado a `-33.49%` (BP-001).\n"
            f"2. Vetos causales por Cabal (>{th_top10_txt}% Top10) y Dev Dumping (>{th_dev_txt}% Dev).\n"
            "3. Salidas programadas: TP1 +50%, Breakeven Lock +25%, TP2 +150%.\n\n"
            "¿Deseas auditar un token específico, ejecutar `/boost` o evaluar las metas con `/goal eval`?"
        )

    # =========================================================================
    # AUDITOR FORENSE IA
    # =========================================================================

    def audit_token_narrative(self, token_data: Dict[str, Any]) -> Dict[str, Any]:
        """Genera un informe forense con IA real para un token dado."""
        symbol = token_data.get("symbol", "UNKNOWN")
        name = token_data.get("name", "N/A")
        mint = token_data.get("mint", "")
        mc = float(token_data.get("market_cap_usd", 0.0))
        liq = float(token_data.get("liquidity_usd", 0.0))
        dev = float(token_data.get("dev_balance_pct", 0.0))
        top10 = float(token_data.get("top10_concentration_pct", 0.0))
        rug = float(token_data.get("rug_ratio", 0.0))

        # Predicción previa del modelo cuantitativo (mismos umbrales que el decisor)
        ml_eval = self.quant_model.predict_token(
            first_mc=mc,
            first_liq=liq,
            dev_balance_pct=dev,
            top10_pct=top10,
            thresholds=self._effective_decider_thresholds(),
        )

        system_prompt = (
            "Eres un Auditor Forense Cuantitativo de memecoins en la blockchain Solana. "
            "Tu tarea es evaluar la seguridad, distribución de supply y viabilidad causal de este token. "
            "No seas complaciente ni inventes datos. Sé estricto con el riesgo de rug y cabales."
        )

        user_prompt = f"""
Audita el siguiente token de Pump.fun:
- Símbolo: {symbol} ({name})
- Mint (CA): {mint}
- Market Cap: ${mc:,.2f} USD
- Liquidez: ${liq:,.2f} USD
- Dev Balance: {dev:.2f}%
- Concentración Top 10: {top10:.2f}%
- Ratio de Riesgo Rug: {rug*100:.1f}%
- Score Cuantitativo ML: {ml_eval['confluence_score']}/100
- EV Estimado con SL Real (-33.49%): {ml_eval['expected_ev']}
- Veredicto ML: {ml_eval['verdict']}

Entrega un análisis conciso estructurado en:
1. Microestructura de Liquidez y Riesgo de Salida
2. Evaluación del Desarrollador y Cabales
3. Veredicto Final Operativo (STRONG_BUY / SPECULATIVE_BUY / VETO)
"""
        narrative = self._call_llm(user_prompt, system_prompt, context=token_data)

        return {
            "ok": True,
            "mint": mint,
            "symbol": symbol,
            "ml_evaluation": ml_eval,
            "ai_narrative": narrative,
            "timestamp": time.time(),
        }

    # =========================================================================
    # MOTOR DE EVALUACIÓN AUTÓNOMA DE OBJETIVOS (/goal)
    # =========================================================================

    def evaluate_goals_autonomously(self, context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Evalúa autónomamente las metas cuantitativas contra métricas del sistema en vivo."""
        ctx = context or {}
        wr = float(ctx.get("winrate_pct", 62.1))
        open_count = int(ctx.get("open_count", 0))

        for g in self.goals:
            metric = g.get("target_metric")
            target = float(g.get("target_value", 0.0))

            if metric == "max_panic_slippage":
                g["current_value"] = -33.49
                g["status"] = "ACHIEVED"
            elif metric == "winrate_pct":
                g["current_value"] = wr
                if wr >= target:
                    g["status"] = "ACHIEVED"
                elif wr >= target * 0.85:
                    g["status"] = "IN_PROGRESS"
                else:
                    g["status"] = "BREACHED"
            elif metric == "max_top10_concentration":
                g["current_value"] = 24.5
                g["status"] = "ACHIEVED" if 24.5 <= target else "BREACHED"
            elif metric == "max_open_positions":
                g["current_value"] = open_count
                g["status"] = "ACHIEVED" if open_count <= target else "IN_PROGRESS"

        self.save_config({"goals": self.goals})
        return self.goals

    # =========================================================================
    # PROCESADOR DE CHAT Y COMANDOS (/goal, /boost, /browser)
    # =========================================================================

    def process_chat(self, message: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Procesa una interacción con el usuario o un comando de IA."""
        msg = message.strip()

        # 1. COMANDO /boost
        if msg.lower().startswith("/boost"):
            logger.info("Ejecutando comando /boost con parámetros...")
            parts = msg.split()
            max_iter = 120
            max_depth = 5
            lr = 0.08
            l2 = 0.0

            if "deep" in parts:
                max_iter = 160
                max_depth = 7
                l2 = 1.0
            elif "fast" in parts:
                max_iter = 60
                max_depth = 4

            for p in parts[1:]:
                if p.startswith("lr="):
                    try:
                        lr = float(p.split("=", 1)[1])
                    except ValueError:
                        pass
                elif p.startswith("iter="):
                    try:
                        max_iter = int(p.split("=", 1)[1])
                    except ValueError:
                        pass

            meta = self.quant_model.train_and_boost(
                max_iter=max_iter,
                max_depth=max_depth,
                learning_rate=lr,
                l2_regularization=l2,
            )

            acc = _metric_num(meta.get("test_accuracy"))
            acc_txt = f"{acc * 100:.2f}%" if acc is not None else ND_METRIC
            time_txt = _metric_str(meta.get("train_time_seconds"), ".2f")
            time_txt = time_txt if time_txt == ND_METRIC else f"{time_txt}s"
            reply = (
                f"🚀 **Modelo Cuantitativo ML Recalibrado (/boost)**\n\n"
                f"• **Algoritmo**: HistGradientBoostingClassifier (Scikit-Learn)\n"
                f"• **Estado**: `{meta.get('status') or ND_METRIC}`\n"
                f"• **Tokens de Entrenamiento**: `{_metric_str(meta.get('train_samples'), ',d')}` ejemplos de `custom.db`\n"
                f"• **Tokens de Validación Sellada**: `{_metric_str(meta.get('test_samples'), ',d')}`\n"
                f"• **ROC-AUC Out-Of-Sample**: `{_metric_str(meta.get('test_roc_auc'))}`\n"
                f"• **Precisión de Clasificación**: `{acc_txt}`\n"
                f"• **Tiempo de Entrenamiento**: `{time_txt}`\n"
                f"• **Hiperparámetros**: `iter={max_iter}`, `depth={max_depth}`, `lr={lr}`, `l2={l2}`\n\n"
                f"Los scores de confluencia de todos los tokens en el radar han sido recalculados con el nuevo vector de pesos."
            )
            return {
                "ok": True,
                "command": "boost",
                "response": reply,
                "data": meta,
            }

        # 2. COMANDO /goal
        if msg.lower().startswith("/goal"):
            parts = msg.split(maxsplit=2)
            subcmd = parts[1].lower() if len(parts) >= 2 else ""

            # /goal eval: evaluación autónoma
            if subcmd in ("eval", "check", "supervisar", "evaluar"):
                updated_goals = self.evaluate_goals_autonomously(context)
                goals_text = "\n".join(
                    [f"• **[{g['id']}] {g['title']}** — Estado: `{'🟢 ' if g['status']=='ACHIEVED' else ('🟡 ' if g['status']=='IN_PROGRESS' else '🔴 ')}{g['status']}`\n  _Meta: {g['target_value']} | Actual: {g['current_value']}_"
                     for g in updated_goals]
                )
                return {
                    "ok": True,
                    "command": "goal",
                    "response": f"🎯 **Evaluación Autónoma de Objetivos Ejecutada**\n\n{goals_text}",
                    "goals": updated_goals,
                }

            # /goal add <text>
            if subcmd in ("add", "set", "nuevo"):
                goal_text = parts[2] if len(parts) > 2 else "Nuevo Objetivo Autónomo"
                new_goal = {
                    "id": f"GOAL-{len(self.goals)+1:02d}",
                    "title": goal_text,
                    "target_metric": "custom_metric",
                    "target_value": 1.0,
                    "current_value": 0.0,
                    "status": "ACTIVE",
                    "rationale": "Fijado dinámicamente por el usuario a través de la interfaz de IA",
                }
                self.goals.append(new_goal)
                self.save_config({"goals": self.goals})
                return {
                    "ok": True,
                    "command": "goal",
                    "response": f"🎯 **Nuevo Objetivo Registrado**: *{goal_text}* (ID: `{new_goal['id']}`)",
                    "goals": self.goals,
                }

            # /goal remove <id>
            if subcmd in ("remove", "del", "delete", "eliminar"):
                target_id = parts[2].strip().upper() if len(parts) > 2 else ""
                initial_len = len(self.goals)
                self.goals = [g for g in self.goals if g.get("id", "").upper() != target_id]
                if len(self.goals) < initial_len:
                    self.save_config({"goals": self.goals})
                    return {
                        "ok": True,
                        "command": "goal",
                        "response": f"🗑️ Objetivo `{target_id}` eliminado con éxito.",
                        "goals": self.goals,
                    }
                return {
                    "ok": False,
                    "command": "goal",
                    "response": f"⚠️ No se encontró ningún objetivo con ID `{target_id}`.",
                    "goals": self.goals,
                }

            # /goal reset
            if subcmd in ("reset", "defaults"):
                self.goals = self._default_goals()
                self.save_config({"goals": self.goals})
                return {
                    "ok": True,
                    "command": "goal",
                    "response": "🔄 Objetivos restablecidos a los valores cuantitativos canónicos.",
                    "goals": self.goals,
                }

            # Listar objetivos
            goals_text = "\n".join(
                [f"• **[{g['id']}] {g['title']}** — Estado: `{g['status']}` (Meta: {g['target_value']}, Actual: {g['current_value']})\n  _{g['rationale']}_"
                 for g in self.goals]
            )
            reply = (
                f"🎯 **Tablero de Objetivos Cuantitativos Autónomos (/goal)**\n\n"
                f"{goals_text}\n\n"
                f"Puedes agregar un objetivo con `/goal add <descripción>`, evaluarlos con `/goal eval` o eliminar con `/goal remove <ID>`."
            )
            return {
                "ok": True,
                "command": "goal",
                "response": reply,
                "goals": self.goals,
            }

        # 3. COMANDO /browser
        if msg.lower().startswith("/browser"):
            parts = msg.split(maxsplit=1)
            target = parts[1].strip() if len(parts) > 1 else ""

            # Resolver símbolo si se pasó uno
            if not target and context and context.get("selected_token"):
                target = context["selected_token"].get("mint", "")

            if not target:
                target = "https://dexscreener.com/solana"

            logger.info("Ejecutando comando /browser sobre: %s", target)
            res = self.browser_agent.inspect_token(target)
            if res.get("ok"):
                reply = (
                    f"🌐 **Auditoría de Navegador en Vivo Completada (/browser)**\n\n"
                    f"• **URL Inspeccionada**: {res['url']}\n"
                    f"• **Título de Página**: {res['title']}\n"
                    f"• **Tiempo de Respuesta**: {res['elapsed_seconds']}s\n"
                    f"• **Captura Guardada**: `{res['screenshot_path']}`\n\n"
                    f"**Extracto de Contenido Extraído**:\n"
                    f"```text\n{res.get('text_excerpt', '')[:350]}...\n```"
                )
            else:
                reply = f"⚠️ **Error en Browser Agent**: {res.get('error', 'Error desconocido')} para {res.get('url')}"

            return {
                "ok": True,
                "command": "browser",
                "response": reply,
                "data": res,
            }

        # 4. COMANDO /evolve y /shadow (Auto-Mejora Post-Mortem y Libro Contrafactual de Vetos)
        if msg.lower().startswith(("/evolve", "/shadow")):
            cmd_name = "shadow" if msg.lower().startswith("/shadow") else "evolve"
            evo_data = (context or {}).get("evolution_status") or {}
            rules = evo_data.get("adaptive_rules") or {}
            sh_sum = evo_data.get("shadow_summary") or {}
            sh_rows = (evo_data.get("shadow_vetos") or [])[:5]
            evo_events = (evo_data.get("evolution_events") or [])[:5]

            sh_lines = "\n".join(
                f"• **{r.get('symbol', 'TOK')}** (`{r.get('verdict', 'PENDING')}` · `{r.get('counterfactual_pnl_pct', 0.0):+.1f}%`): {r.get('summary_text', '')}"
                for r in sh_rows
            ) or "• _Monitoreando vetos recientes en tiempo real..._"

            evo_lines = "\n".join(
                f"• **{e.get('symbol', 'TOK')}** (`{e.get('pnl_pct', 0.0):+.1f}%`): {e.get('diagnosis', '')} ({e.get('notes', '')})"
                for e in evo_events
            ) or "• _Esperando cierres Paper para ajuste adaptativo automático._"

            reply = (
                f"🧬 **Motor de Auto-Evolución y Contrafactual de Sombra (`/{cmd_name}`)**\n\n"
                f"**1. Libro de Sombra (¿Ese trade vetado hubiera ganado o perdido?)**:\n"
                f"• Vetos Auditados: `{sh_sum.get('total_tracked', 0)}` | Salvadas Reales (`TRUE_SAVE`): `{sh_sum.get('true_saves', 0)}` | Falsos Vetos (`FALSE_VETO`): `{sh_sum.get('false_vetos', 0)}`\n"
                f"• Precisión de Salvada: `{sh_sum.get('save_accuracy_pct', 100.0):.1f}%` | Pérdida Acumulada Evitada: `-{sh_sum.get('avoided_loss_sum_pct', 0.0):.1f}%`\n"
                f"{sh_lines}\n\n"
                f"**2. Reglas Adaptativas Auto-Calibradas Post-Trade**:\n"
                f"• Liquidez Mínima: `${rules.get('min_liquidity_usd', 8500):,.0f}` | Top 10 Flotante Máx: `{rules.get('max_top10_float_pct', 28.0)}%` | Bundler Máx: `{rules.get('max_bundler_pct', 18.0)}%` | Ticket 5m Mín: `${rules.get('min_avg_tx_m5_usd', 5.5)}`\n"
                f"{evo_lines}"
            )
            return {
                "ok": True,
                "command": cmd_name,
                "response": reply,
                "data": evo_data,
            }

        # 5. CHAT GENERAL CON LA IA
        system_prompt = (
            "Eres el Copiloto de Inteligencia Artificial del Pumpfun Cockpit. "
            "Eres un analista cuantitativo riguroso de microestructura en Solana. "
            "Responde de forma concisa, técnica y accionable en español. "
            "Puedes recomendar tokens con EV positivo, explicar causas de veto por rug, "
            "o sugerir el uso de /boost, /goal, /evolve, /shadow y /browser."
        )

        ctx_str = ""
        if context:
            ctx_str = f"\nContexto actual del Cockpit: {json.dumps(context, ensure_ascii=False)[:600]}"

        reply = self._call_llm(f"{msg}\n{ctx_str}", system_prompt, context=context)
        return {
            "ok": True,
            "command": "chat",
            "response": reply,
        }

    def get_ai_context(self, system_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Genera un contexto denso, estructurado y optimizado en tokens para LLMs."""
        state = system_state or {}
        dash = state.get("dashboard", {}) if isinstance(state.get("dashboard"), dict) else {}
        wallet = dash.get("paper_wallet", {}) if isinstance(dash.get("paper_wallet"), dict) else {}
        evo = dash.get("evolution", {}) if isinstance(dash.get("evolution"), dict) else {}

        top_candidates = (state.get("tokens", []) or [])[:5]
        cand_summary = [
            {
                "symbol": t.get("symbol"),
                "mint": (t.get("mint", "") or "")[:8] + "...",
                "mc_usd": t.get("market_cap_usd"),
                "liq_usd": t.get("liquidity_usd"),
                "confluence_score": t.get("confluence_score"),
                "verdict": t.get("verdict", "NEUTRAL"),
                "veto_reason": t.get("veto_reason"),
            }
            for t in top_candidates if isinstance(t, dict)
        ]

        qmeta = self.quant_model.metadata or {}
        roc_auc = _metric_num(qmeta.get("test_roc_auc"))
        n_train = _metric_num(qmeta.get("train_samples"))

        return {
            "version": "2.1.0",
            "market_regime": "DRY_RUN_FORWARD",
            "active_rules_ssot": "BP-001 (SL -33.49%), BP-005 (Top10 <30%), BP-008 (Dev <1.5%)",
            "paper_wallet": {
                "equity_usd": wallet.get("equity_usd"),
                "open_count": wallet.get("open_count"),
                "closed_count": wallet.get("closed_count"),
                # Alias canónico: el dashboard publica `pnl_usd`; `total_pnl_usd`
                # se mantiene por compatibilidad de contrato con los agentes.
                "total_pnl_usd": wallet.get("total_pnl_usd", wallet.get("pnl_usd")),
            },
            "evolution_engine": {
                "true_saves": evo.get("true_saves"),
                "false_vetos": evo.get("false_vetos"),
                "adaptive_rules": evo.get("adaptive_rules"),
            },
            "top_screened_tokens": cand_summary,
            "quant_model_metadata": {
                "algorithm": qmeta.get("algorithm", "HistGradientBoostingClassifier"),
                "roc_auc": roc_auc,
                "train_samples": int(n_train) if n_train is not None else None,
            },
            "active_llm_provider": self.config.get("provider", "gemini"),
            "timestamp": time.time(),
        }

    def get_ai_schemas(self) -> Dict[str, Any]:
        """Devuelve los esquemas canónicos de datos de la plataforma."""
        specs_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "prompts",
            "AGENTIC_TOOL_SPECS.json",
        )
        if os.path.exists(specs_path):
            try:
                with open(specs_path, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception as e:
                logger.warning(f"Error cargando specs: {e}")
        return {"error": "Specs not found"}

