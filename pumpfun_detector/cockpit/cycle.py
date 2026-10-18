"""Ciclo automático: recolectar -> snapshot -> barrido causal -> reportar.

Un único punto de entrada que deja el estado honesto listo para el cockpit:

    1. Snapshot del universo (solo lectura) + análisis de sesgo de lookahead.
    2. Barrido causal de umbrales con gate OOS (muestra + IC + EV > 0).
    3. Escribe ``cycle.json`` en el runtime del cockpit.

Nunca escribe en la base. Se puede ejecutar una vez o en bucle
(``--loop --interval-hours 24``) para dejarlo desatendido.

Uso
---
    python -m cockpit.cycle                      # una pasada
    python -m cockpit.cycle --loop --interval-hours 24
    python -m cockpit.cycle --db ... --mapping mapping_pumpscope.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cockpit import snapshot as snap_mod

RUNTIME_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime")
DEFAULT_CYCLE = os.path.join(RUNTIME_DIR, "cycle.json")


def _run_sweep(db: str, mapping: Optional[str], out_dir: str, min_oos_n: int,
               quantiles: str) -> Dict[str, Any]:
    """Barrido causal con gate honesto. Devuelve un resumen serializable."""
    try:
        import pandas as pd
        from scripts.causal_sweep import (
            LABEL, _feature_available, _metrics, load_trades, pairs,
            select_promoted, univariate,
        )
    except Exception as exc:  # pragma: no cover - defensivo
        return {"ok": False, "error": f"import: {type(exc).__name__}: {exc}"}

    try:
        trades = load_trades(db, mapping, out_dir)
        if LABEL not in trades.columns:
            return {"ok": False, "error": f"falta la etiqueta {LABEL}"}
        if "created_at" not in trades.columns:
            return {"ok": False, "error": "sin columna created_at: no hay partición temporal"}
        trades = trades.dropna(subset=[LABEL]).sort_values("created_at").reset_index(drop=True)
        n = len(trades)
        if n < 100:
            return {"ok": False, "error": f"muestra insuficiente (n={n})"}
        n_tr, n_va = int(n * 0.6), int(n * 0.2)
        train = trades.iloc[:n_tr]
        test = trades.iloc[n_tr + n_va:]
        feats = _feature_available(train)
        qs = tuple(float(x) for x in quantiles.split(","))
        uni = univariate(train, test, feats, qs)
        prs = pairs(train, test, feats, qs)
        base_oos = _metrics(test, LABEL)
        promoted = select_promoted(uni + prs, min_oos_n, base_oos["winrate"] or 0.0)
        return {
            "ok": True,
            "label": LABEL,
            "n": n, "train": len(train), "oos": len(test),
            "baseline_oos_wr": base_oos["winrate"],
            "n_candidates": len(uni) + len(prs),
            "n_promoted": len(promoted),
            "promoted": promoted[:10],
            "verdict": ("EDGE CANDIDATO" if promoted else
                        "SIN EDGE DEMOSTRADO (0 reglas superan el gate OOS)"),
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def run_cycle(db: str, mapping: Optional[str], out_dir: str = RUNTIME_DIR,
              sample: int = 3000, min_oos_n: int = 30,
              quantiles: str = "0.1,0.2,0.3,0.5,0.7,0.8,0.9",
              cycle_path: str = DEFAULT_CYCLE,
              refresh_features: bool = False) -> Dict[str, Any]:
    """Una pasada completa del ciclo. Devuelve el dict del ciclo.

    ``refresh_features=True`` invalida la caché de features para que el barrido
    vea los datos NUEVOS que PumpScope haya recolectado (si no, reutiliza el
    parquet y el resultado no cambia).
    """
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()

    if refresh_features:
        cache = os.path.join(out_dir, "causal_trades.parquet")
        try:
            if os.path.exists(cache):
                os.remove(cache)
        except OSError:
            pass

    snap = snap_mod.build_snapshot(db, sample=sample)
    snap_mod.write_snapshot(snap, os.path.join(out_dir, "snapshot.json"))

    sweep = _run_sweep(db, mapping, out_dir, min_oos_n, quantiles)

    cycle: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seconds": round(time.time() - t0, 1),
        "source": {"db": os.path.basename(db), "read_only": True},
        "universe": snap.get("universe"),
        "bias": snap.get("bias"),
        "sweep": sweep,
    }
    tmp = cycle_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cycle, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, cycle_path)
    return cycle


def _print(cycle: Dict[str, Any]) -> None:
    u = cycle.get("universe") or {}
    s = cycle.get("sweep") or {}
    print(f"[cycle] {cycle.get('generated_at')} en {cycle.get('seconds')}s")
    r50 = u.get("rise50_pct")
    r50_txt = "no medible (falta rise50At)" if r50 is None else f"{r50}%"
    print(f"[cycle] universo: {u.get('n_tokens')} tokens | +50%={r50_txt} "
          f"| WR real={u.get('paper_wr_pct')}%")
    if s.get("ok"):
        print(f"[cycle] barrido: {s.get('n_candidates')} candidatos | "
              f"promovidos={s.get('n_promoted')} | {s.get('verdict')}")
        for r in s.get("promoted", [])[:5]:
            name = r.get("rule") or f"{r.get('feature')} {r.get('op')} {r.get('threshold')}"
            print(f"        [+] {name} | OOS n={r.get('oos_n')} WR={r.get('oos_wr')}% "
                  f"EV={r.get('oos_ev')}%")
    else:
        print(f"[cycle] barrido: ERROR {s.get('error')}")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Ciclo automático del detector (solo lectura).")
    p.add_argument("--db", default=snap_mod.DEFAULT_DB)
    p.add_argument("--mapping", default="mapping_pumpscope.json")
    p.add_argument("--out", default=RUNTIME_DIR)
    p.add_argument("--sample", type=int, default=3000)
    p.add_argument("--min-oos-n", type=int, default=30)
    p.add_argument("--quantiles", default="0.1,0.2,0.3,0.5,0.7,0.8,0.9")
    p.add_argument("--loop", action="store_true", help="Repetir indefinidamente.")
    p.add_argument("--interval-hours", type=float, default=24.0)
    p.add_argument("--refresh-features", action="store_true",
                   help="Reconstruir las features en cada pasada (ver datos nuevos).")
    p.add_argument("--max-hours", type=float, default=0.0,
                   help="Detener el bucle tras N horas (0 = sin límite).")
    return p


def main(argv: Optional[list] = None) -> int:
    args = _build_parser().parse_args(argv)
    started = time.time()
    deadline = started + args.max_hours * 3600.0 if args.max_hours > 0 else None
    n = 0
    while True:
        n += 1
        try:
            cycle = run_cycle(args.db, args.mapping, args.out, args.sample,
                              args.min_oos_n, args.quantiles,
                              refresh_features=args.refresh_features)
            _print(cycle)
        except KeyboardInterrupt:
            print("\n[cycle] detenido por el usuario.")
            return 0
        except Exception as exc:
            print(f"[cycle] error: {type(exc).__name__}: {exc}")
        if not args.loop:
            return 0
        if deadline is not None and time.time() >= deadline:
            print(f"[cycle] límite de {args.max_hours}h alcanzado tras {n} pasada(s).")
            return 0
        secs = max(60.0, args.interval_hours * 3600.0)
        if deadline is not None:
            secs = min(secs, max(1.0, deadline - time.time()))
        print(f"[cycle] pasada {n} lista. Siguiente en {secs / 60:.0f} min. "
              f"Ctrl+C para salir.")
        try:
            time.sleep(max(1.0, secs))
        except KeyboardInterrupt:
            print("\n[cycle] detenido por el usuario.")
            return 0


if __name__ == "__main__":
    sys.exit(main())
