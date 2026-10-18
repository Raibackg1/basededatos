"""
Auto-Evolution & Counterfactual Shadow Engine — Bucle Cerrado de Auto-Mejora Cuantitativa.

Resuelve los dos pilares de autonomía real en Paper Forward:
1. Contrafactual de Vetos ("Autodecir: ¿ese trade vetado hubiera perdido o ganado?"):
   - Registra cada token rechazado con su Market Cap en el instante del veto.
   - A medida que pasa el tiempo, consulta el precio real en DexScreener (`MarketFeed`)
     y clasifica el desenlace como `TRUE_SAVE` (el filtro salvó de una pérdida real)
     o `FALSE_VETO` (el token subió limpiamente).
2. Post-Mortem Forense y Auto-Calibración tras Cierre Paper ("Si abrí y perdí, automejorar"):
   - Cuando una posición Paper se cierra (por TP, Peak Trailing Stop, Stall Timeout o SL -33.49%),
     diagnostica la causa raíz (`BUNDLER_DUMP`, `WASH_COLLAPSE`, `STALL_BLEED`, `DEV_CEILING`),
     etiqueta automáticamente el token en `AnnotationDB` (`BUY` o `AVOID`) para reentrenar
     el modelo `AIQuantModel`, ajusta los umbrales adaptativos (`adaptive_rules.json`)
     y persiste cada lección en SQLite (`auto_evolution_log`) y JSONL (`autonomous_evolution.jsonl`).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_ADAPTIVE_RULES: Dict[str, Any] = {
    "min_liquidity_usd": 8500.0,
    "max_top10_float_pct": 28.0,
    "max_bundler_pct": 18.0,
    "min_avg_tx_m5_usd": 5.5,
    "min_vol_to_mc_h1_pct": 2.5,
    "min_composite_dd_score": 70.0,
    "trail_activate_pct": 30.0,
    "trail_drop_pct": 13.0,
    "stall_timeout_sec": 300,
    "evolutions_count": 0,
    "last_updated_ts": 0.0,
    # Puerta BP-003 (AGENTS.md §4): muestra forward acumulada y mapa de reglas
    # ajustadas con su N de respaldo. Un umbral adaptado sólo puede mover al
    # decisor cuando `n_support >= 30`; antes de eso queda en su literal.
    "closed_trades_seen": 0,
    "adjusted_keys": {},
}


def _sf(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def _extract_market_cap(snap: Any) -> float:
    """Extrae market cap USD de un snapshot (dict, número o atributo). 0.0 si no hay dato."""
    if snap is None or isinstance(snap, bool):
        return 0.0
    if isinstance(snap, (int, float)):
        return _sf(snap, 0.0)
    if isinstance(snap, dict):
        return _sf(snap.get("market_cap_usd"), 0.0)
    return _sf(getattr(snap, "market_cap_usd", None), 0.0)


def feed_market_cap_usd(feed: Any, mint: str) -> Optional[float]:
    """Market cap real vía la API pública del feed, o ``None`` (fail-closed).

    Contrato con `MarketFeed` real: `market_cap_usd()` y/o `token_snapshot()`
    (ambos existen en producción). Compatibilidad con dobles legacy que exponen
    `fetch_token()`. Nunca lanza y nunca inventa un valor.
    """
    if feed is None or not mint:
        return None
    for attr in ("market_cap_usd", "token_snapshot", "fetch_token"):
        fn = getattr(feed, attr, None)
        if not callable(fn):
            continue
        try:
            result = fn(mint)
        except Exception as exc:  # el feed real puede fallar por red; no rompemos el tick
            logger.debug("feed.%s(%s) falló: %s", attr, str(mint)[:12], exc)
            continue
        mc = _extract_market_cap(result)
        if mc > 0.0:
            return mc
    return None


class AutoEvolutionEngine:
    """Motor autónomo de evaluación contrafactual (Shadow Vetos) y auto-mejora post-trade."""

    def __init__(
        self,
        store: Optional[Any] = None,
        annotation_db: Optional[Any] = None,
        runtime_dir: Optional[Path] = None,
    ) -> None:
        self.store = store
        self.annotation_db = annotation_db
        base_dir = runtime_dir or (Path(__file__).resolve().parent.parent / "cockpit" / "runtime")
        self.runtime_dir = Path(base_dir)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.rules_path = self.runtime_dir / "adaptive_rules.json"
        self.jsonl_path = self.runtime_dir / "autonomous_evolution.jsonl"
        self.rules = self._load_rules()
        # Observabilidad del ticker contrafactual (anti-falacia: nunca silencioso)
        self.shadow_miss_total = 0
        self.last_shadow_tick: Dict[str, Any] = {}

    # Puerta BP-003 (AGENTS.md §4): un umbral adaptado sólo puede mover al
    # decisor si la muestra forward que lo respalda alcanza N≥30 cierres.
    DECIDER_GATE_MIN_N = 30

    def _load_rules(self) -> Dict[str, Any]:
        merged = dict(DEFAULT_ADAPTIVE_RULES)
        if self.rules_path.exists():
            try:
                data = json.loads(self.rules_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    merged.update(data)
            except Exception as exc:
                logger.debug("No se pudo leer adaptive_rules.json: %s", exc)
        return merged

    def _save_rules(self) -> None:
        self.rules["last_updated_ts"] = round(time.time(), 2)
        try:
            self.rules_path.write_text(
                json.dumps(self.rules, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("No se pudo guardar adaptive_rules.json: %s", exc)

    def _append_jsonl(self, payload: Dict[str, Any]) -> None:
        try:
            with self.jsonl_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug("No se pudo escribir autonomous_evolution.jsonl: %s", exc)

    def get_effective_decider_thresholds(self) -> Dict[str, Dict[str, Any]]:
        """Umbrales EFECTIVOS del decisor con la puerta BP-003 aplicada.

        Ley (AGENTS.md §4): prohibido alterar umbrales del decisor con muestras
        menores a 30 operaciones forward. Reglas:

        - Regla nunca ajustada → literal del código (``default_literal``).
        - Regla ajustada con ``n_support < 30`` → bloqueada (literal).
        - Regla ajustada con ``n_support >= 30`` → el valor adaptado mueve al
          decisor y viaja declarado (``adaptive_rule``) con su procedencia.

        Devuelve, por clave de ``AIQuantModel.predict_token(thresholds=...)``:
        ``value / source / unlocked / n_support / min_n / closed_trades_seen``.
        """
        # clave decisor → (clave interna de la regla, literal actual del decisor)
        mapping = {
            "max_bundler_pct": ("max_bundler_pct", 20.0),
            "max_top10_pct": ("max_top10_float_pct", 30.0),
        }
        adjusted = self.rules.get("adjusted_keys")
        adjusted = adjusted if isinstance(adjusted, dict) else {}
        closed_seen = int(self.rules.get("closed_trades_seen", 0) or 0)
        out: Dict[str, Dict[str, Any]] = {}
        for decider_key, (rule_key, default_v) in mapping.items():
            entry = adjusted.get(rule_key)
            entry = entry if isinstance(entry, dict) else {}
            n_support = int(entry.get("n_support", 0) or 0)
            raw_value = entry.get("value")
            unlocked = (
                bool(entry)
                and n_support >= self.DECIDER_GATE_MIN_N
                and isinstance(raw_value, (int, float))
                and not isinstance(raw_value, bool)
            )
            out[decider_key] = {
                "value": float(raw_value) if unlocked else float(default_v),
                "source": "adaptive_rule" if unlocked else "default_literal",
                "unlocked": unlocked,
                "n_support": n_support,
                "min_n": self.DECIDER_GATE_MIN_N,
                "closed_trades_seen": closed_seen,
            }
        return out

    def register_vetoed_token(
        self,
        mint: str,
        symbol: str,
        veto_reason: str,
        entry_mc_usd: float,
        ts: Optional[float] = None,
    ) -> None:
        """Registra un token vetado para evaluar su desenlace real con el paso del tiempo."""
        if self.store is not None and hasattr(self.store, "upsert_shadow_veto"):
            try:
                self.store.upsert_shadow_veto(
                    mint=mint,
                    symbol=symbol,
                    veto_reason=veto_reason,
                    entry_mc_usd=entry_mc_usd,
                    ts=ts,
                )
            except Exception as exc:
                logger.debug("Error registrando shadow veto: %s", exc)

    def tick_shadow_veto_outcomes(
        self,
        feed: Optional[Any] = None,
        price_overrides: Optional[Dict[str, float]] = None,
        max_check: int = 12,
    ) -> List[Dict[str, Any]]:
        """Actualiza con precios reales los tokens vetados y responde si hubieran ganado o perdido.

        Rotación honesta: se evalúan primero los vetos menos recientemente chequeados
        (`list_shadow_vetos_for_check`), de modo que ningún veto viejo quede en
        `PENDING` eterno. Si el feed no da precio, la fila queda intacta y la falla
        se contabiliza en `last_shadow_tick` / `shadow_miss_total` (nunca silencio).
        """
        if self.store is None or not hasattr(self.store, "list_shadow_vetos"):
            return []

        if hasattr(self.store, "list_shadow_vetos_for_check"):
            rows = self.store.list_shadow_vetos_for_check(limit=max_check)
        else:
            rows = self.store.list_shadow_vetos(limit=max_check)
        updated_rows: List[Dict[str, Any]] = []
        overrides = dict(price_overrides or {})
        misses = 0
        hits = 0

        for r in rows:
            mint = str(r.get("mint") or "").strip()
            if not mint:
                continue
            cur_mc = _sf(overrides.get(mint), 0.0)
            if cur_mc <= 0.0:
                mc = feed_market_cap_usd(feed, mint)
                cur_mc = mc if mc is not None else 0.0
            if cur_mc > 0.0:
                upd = self.store.update_shadow_veto_price(mint, cur_mc)
                if upd:
                    hits += 1
                    updated_rows.append(upd)
                    continue
            misses += 1
            updated_rows.append(r)

        self.shadow_miss_total += misses
        self.last_shadow_tick = {
            "ts": round(time.time(), 3),
            "checked": len(rows),
            "hits": hits,
            "misses": misses,
            "has_feed": feed is not None,
        }
        if misses:
            logger.info(
                "Shadow ticker: %d/%d vetos sin precio real (feed=%s); acumulado=%d",
                misses, len(rows), type(feed).__name__ if feed is not None else "None",
                self.shadow_miss_total,
            )
        return updated_rows

    def diagnose_and_learn_from_closed_trade(
        self,
        pos_dict: Dict[str, Any],
        token_features: Optional[Dict[str, Any]] = None,
        dossier_dict: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Ejecuta post-mortem automático al cerrar un trade Paper, etiqueta y auto-corrige reglas."""
        mint = str(pos_dict.get("mint") or "")
        symbol = str(pos_dict.get("symbol") or "UNKNOWN")
        size_sol = max(_sf(pos_dict.get("size_sol"), 0.5), 0.01)
        realized_sol = _sf(pos_dict.get("realized_pnl_sol"), 0.0)
        pnl_pct = round((realized_sol / size_sol) * 100.0, 2)
        exit_reason = str(pos_dict.get("exit_reason") or pos_dict.get("status") or "CLOSED")

        feats = dict(token_features or {})
        if dossier_dict:
            for k, v in dossier_dict.items():
                feats.setdefault(k, v)

        liq_usd = _sf(feats.get("liquidity_usd"), _sf(pos_dict.get("entry_mc_usd")) * 0.25)
        top10_float = _sf(feats.get("top10_float_pct", feats.get("top10_concentration_pct")), 20.0)
        bundler_pct = _sf(feats.get("bundler_pct", feats.get("sniper_pct")), 0.0)
        avg_tx_m5 = _sf(feats.get("avg_tx_m5_usd"), 15.0)
        dd_score = _sf(feats.get("composite_dd_score"), 70.0)

        # Muestra forward acumulada (puerta BP-003): cada cierre procesado cuenta,
        # con o sin ajuste — es el N que respalda cualquier umbral adaptado.
        closed_seen = int(self.rules.get("closed_trades_seen", 0) or 0) + 1
        self.rules["closed_trades_seen"] = closed_seen

        rule_adjusted = ""
        old_val_str = ""
        new_val_str = ""
        notes_parts: List[str] = []

        if pnl_pct < 0.0:
            event_type = "LOSS_POSTMORTEM_ADAPTATION"
            # Clasificar causa raíz de la pérdida y apretar el filtro correspondiente
            if bundler_pct >= 12.0 or bool(feats.get("bundler_flag")):
                diagnosis = (
                    f"PÉRDIDA POR DESCARGA DE BUNDLER ({pnl_pct:+.1f}%): el cluster empaquetado "
                    f"({bundler_pct:.1f}%) absorbió la liquidez tras la entrada."
                )
                old_v = float(self.rules.get("max_bundler_pct", 18.0))
                new_v = round(max(8.0, min(old_v - 1.5, bundler_pct - 1.0)), 2)
                self.rules["max_bundler_pct"] = new_v
                rule_adjusted = "max_bundler_pct"
                old_val_str, new_val_str = f"{old_v}%", f"{new_v}%"
            elif avg_tx_m5 < 8.5 or bool(feats.get("wash_trading_flag")):
                diagnosis = (
                    f"PÉRDIDA POR VOLUMEN HUECO / WASH-TRADING ({pnl_pct:+.1f}%): ticket medio 5m "
                    f"(${avg_tx_m5:.2f}) insuficiente para sostener salida real."
                )
                old_v = float(self.rules.get("min_avg_tx_m5_usd", 5.5))
                new_v = round(min(20.0, max(old_v + 1.0, avg_tx_m5 + 1.2)), 2)
                self.rules["min_avg_tx_m5_usd"] = new_v
                rule_adjusted = "min_avg_tx_m5_usd"
                old_val_str, new_val_str = f"${old_v}", f"${new_v}"
            elif top10_float > 22.0:
                diagnosis = (
                    f"PÉRDIDA POR CONCENTRACIÓN DE FLOTANTE ({pnl_pct:+.1f}%): Top 10 poseía "
                    f"{top10_float:.1f}% del suministro negociable."
                )
                old_v = float(self.rules.get("max_top10_float_pct", 28.0))
                new_v = round(max(15.0, min(old_v - 1.5, top10_float - 1.0)), 2)
                self.rules["max_top10_float_pct"] = new_v
                rule_adjusted = "max_top10_float_pct"
                old_val_str, new_val_str = f"{old_v}%", f"{new_v}%"
            elif "STALL" in exit_reason.upper():
                diagnosis = (
                    f"PÉRDIDA POR ESTANCAMIENTO / STALL ({pnl_pct:+.1f}%): el flujo comprador se agotó "
                    f"antes de alcanzar TP1."
                )
                old_v = float(self.rules.get("min_vol_to_mc_h1_pct", 2.5))
                new_v = round(min(10.0, old_v + 0.5), 2)
                self.rules["min_vol_to_mc_h1_pct"] = new_v
                rule_adjusted = "min_vol_to_mc_h1_pct"
                old_val_str, new_val_str = f"{old_v}%", f"{new_v}%"
            else:
                diagnosis = (
                    f"PÉRDIDA EN CURVA MICRO-CAP ({pnl_pct:+.1f}% · salida: {exit_reason}): "
                    f"liquidez de entrada ${liq_usd:,.0f} USD no soportó el retroceso."
                )
                old_v = float(self.rules.get("min_liquidity_usd", 8500.0))
                new_v = round(min(25000.0, max(old_v + 750.0, liq_usd * 1.08)), 0)
                self.rules["min_liquidity_usd"] = new_v
                rule_adjusted = "min_liquidity_usd"
                old_val_str, new_val_str = f"${old_v:,.0f}", f"${new_v:,.0f}"

            # Trazabilidad BP-003: valor ajustado + N de muestra que lo respalda.
            if rule_adjusted:
                adjusted = self.rules.setdefault("adjusted_keys", {})
                if not isinstance(adjusted, dict):
                    adjusted = {}
                    self.rules["adjusted_keys"] = adjusted
                adjusted[rule_adjusted] = {
                    "value": self.rules.get(rule_adjusted),
                    "n_support": closed_seen,
                    "ts": round(time.time(), 2),
                }
            self.rules["evolutions_count"] = int(self.rules.get("evolutions_count", 0)) + 1
            self._save_rules()
            notes_parts.append(f"Filtro '{rule_adjusted}' ajustado de {old_val_str} a {new_val_str}")
        else:
            event_type = "WIN_PATTERN_REINFORCEMENT"
            diagnosis = (
                f"GANANCIA REALIZADA ({pnl_pct:+.1f}% · {realized_sol:+.4f} SOL · salida: {exit_reason}): "
                f"estructura limpia confirmada (DD Score {dd_score:.0f}/100, Top10 {top10_float:.1f}%)."
            )
            notes_parts.append("Patrón ganador etiquetado como BUY en AnnotationDB para reforzar AIQuantModel")
            self._save_rules()  # persistir la muestra acumulada (contador BP-003)

        # Auto-anotar en AnnotationDB para que AIQuantModel aprenda de este resultado real
        if self.annotation_db is not None and mint:
            try:
                label = "BUY" if pnl_pct > 5.0 else "AVOID"
                safe_feats = {
                    k: v for k, v in feats.items()
                    if isinstance(v, (int, float, str, bool)) or v is None
                }
                safe_feats["outcome_pnl_pct"] = pnl_pct
                safe_feats["exit_reason"] = exit_reason
                self.annotation_db.add_annotation(
                    mint=mint,
                    symbol=symbol,
                    decision=label,
                    rationale=diagnosis,
                    tags=["auto_postmortem", exit_reason.lower()],
                    features_snapshot=safe_feats,
                )
            except Exception as exc:
                logger.warning("No se pudo auto-anotar resultado paper en AnnotationDB: %s", exc)

        notes_str = " | ".join(notes_parts)
        event_id = 0
        if self.store is not None and hasattr(self.store, "record_evolution_event"):
            try:
                event_id = self.store.record_evolution_event(
                    event_type=event_type,
                    mint=mint,
                    symbol=symbol,
                    pnl_pct=pnl_pct,
                    diagnosis=diagnosis,
                    rule_adjusted=rule_adjusted,
                    old_val=old_val_str,
                    new_val=new_val_str,
                    notes=notes_str,
                )
            except Exception as exc:
                logger.debug("No se pudo guardar evento en auto_evolution_log: %s", exc)

        event_payload = {
            "id": event_id,
            "ts": time.time(),
            "event_type": event_type,
            "mint": mint,
            "symbol": symbol,
            "pnl_pct": pnl_pct,
            "exit_reason": exit_reason,
            "diagnosis": diagnosis,
            "rule_adjusted": rule_adjusted,
            "old_val": old_val_str,
            "new_val": new_val_str,
            "notes": notes_str,
        }
        self._append_jsonl(event_payload)
        return event_payload

    def get_status_summary(self) -> Dict[str, Any]:
        """Devuelve el estado completo del motor de auto-evolución y del libro de sombra (Shadow Vetos)."""
        shadow_rows: List[Dict[str, Any]] = []
        evo_events: List[Dict[str, Any]] = []
        if self.store is not None:
            if hasattr(self.store, "list_shadow_vetos"):
                shadow_rows = self.store.list_shadow_vetos(limit=30)
            if hasattr(self.store, "list_evolution_events"):
                evo_events = self.store.list_evolution_events(limit=30)

        true_saves = sum(1 for r in shadow_rows if r.get("verdict") == "TRUE_SAVE")
        false_vetos = sum(1 for r in shadow_rows if r.get("verdict") == "FALSE_VETO")
        evaluated = true_saves + false_vetos
        save_accuracy_pct = round((true_saves / evaluated) * 100.0, 1) if evaluated > 0 else 100.0
        avoided_loss_sum_pct = round(
            sum(abs(_sf(r.get("counterfactual_pnl_pct"))) for r in shadow_rows if _sf(r.get("counterfactual_pnl_pct")) < 0),
            1,
        )

        return {
            "adaptive_rules": dict(self.rules),
            "decider_thresholds": self.get_effective_decider_thresholds(),
            "shadow_summary": {
                "total_tracked": len(shadow_rows),
                "true_saves": true_saves,
                "false_vetos": false_vetos,
                "save_accuracy_pct": save_accuracy_pct,
                "avoided_loss_sum_pct": avoided_loss_sum_pct,
            },
            "shadow_vetos": shadow_rows,
            "evolution_events": evo_events,
        }
