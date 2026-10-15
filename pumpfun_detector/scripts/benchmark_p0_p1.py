"""Benchmark seguro antes/después para P0+P1; nunca modifica la base fuente."""
from __future__ import annotations
import json, shutil, sqlite3, statistics, tempfile, time
from pathlib import Path
from detector.db_optimizer import DatabaseEngineOptimizer
from detector.execution_guards import QuoteGate, safe_position_size
from detector.master_signal import MasterSnipeSignal

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "demo_pumpfun.db"
OUT = ROOT.parent / "outputs" / "p0_p1_comparison.md"


def timed(conn, query, params=(), repeats=5):
    vals = []
    for _ in range(repeats):
        t = time.perf_counter(); conn.execute(query, params).fetchall(); vals.append((time.perf_counter()-t)*1000)
    return round(statistics.median(vals), 4)


def main():
    if not SOURCE.exists():
        raise FileNotFoundError(SOURCE)
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "baseline.db"; opt = Path(td) / "optimized.db"
        shutil.copy2(SOURCE, base); shutil.copy2(SOURCE, opt)
        queries = [
            ("trades por token", "SELECT * FROM trades WHERE token_mint=?", ("missing",)),
            ("trades ordenados", "SELECT * FROM trades ORDER BY block_time DESC LIMIT 50", ()),
            ("my_trades por token", "SELECT * FROM my_trades WHERE token_mint=?", ("missing",)),
        ]
        c0 = sqlite3.connect(base); before = {name: timed(c0, q, p) for name,q,p in queries}; c0.close()
        report = DatabaseEngineOptimizer(str(opt)).analyze()
        apply = DatabaseEngineOptimizer(str(opt)).apply_optimizations(report)
        c1 = sqlite3.connect(opt); after = {name: timed(c1, q, p) for name,q,p in queries}; c1.close()
        plans = {}
        c1 = sqlite3.connect(opt)
        for name,q,p in queries:
            plans[name] = [r[-1] for r in c1.execute("EXPLAIN QUERY PLAN " + q, p).fetchall()]
        c1.close()

    samples = [
        {"market_cap": 6000, "firstMarketCap": 3500, "firstLiquidity": 1200, "realSolReserves": 20, "devBalancePct": 0.5, "n_buys_60s": 3},
        {"market_cap": 9000, "firstMarketCap": 3500, "firstLiquidity": 1200, "realSolReserves": 20, "devBalancePct": 0.5, "n_buys_60s": 3},
        {"market_cap_at_entry": 9000, "firstMarketCap": 3500, "firstLiquidity": 1200, "realSolReserves": 20, "devBalancePct": 0.5, "n_buys_60s": 3},
    ]
    strict = [MasterSnipeSignal.evaluate(x, strict_asof=True).admitted for x in samples]
    legacy = [MasterSnipeSignal.evaluate(x, strict_asof=False).admitted for x in samples]
    quote_veto = not QuoteGate.evaluate({"quoted_at_ms": 0, "price_impact_pct": 9, "liquidity_sol": 20, "route": "pump"}, 5000).allowed
    sizing_zero = safe_position_size(10, -0.1, 100, 0) == 0.0
    lines = ["# Comparación P0+P1 (benchmark local reproducible)", "", f"- **Fuente:** `{SOURCE.name}`", "- **Procedencia:** `synthetic` (no evidencia forward)", "- **Base original:** no modificada; se usaron copias temporales.", "", "## Gates y anti-leakage", "", f"| Prueba | Antes/compatibilidad | Después P0+P1 |", "|---|---:|---:|", f"| Decisiones admitidas en muestras (legacy) | {sum(legacy)} | {sum(strict)} |", f"| Rechazo quote stale/impacto | no medido | {'OK' if quote_veto else 'FAIL'} |", f"| Sizing EV negativo devuelve 0 | no medido | {'OK' if sizing_zero else 'FAIL'} |", "", "La muestra contiene dos vectores con `market_cap` final sin as-of y uno con `market_cap_at_entry`; el modo estricto rechaza los dos primeros. Esto demuestra prevención de contaminación, no una mejora de rentabilidad.", "", "## SQLite", "", "| Consulta | Antes ms | Después ms | Variación |", "|---|---:|---:|---:|"]
    for name in before:
        delta = ((after[name] - before[name]) / before[name] * 100) if before[name] else 0
        lines.append(f"| {name} | {before[name]:.4f} | {after[name]:.4f} | {delta:+.1f}% |")
    lines += ["", f"Índices aplicados: {len(apply.get('applied_indexes', []))}.", "", "### Planes posteriores", ""]
    for name, p in plans.items(): lines.append(f"- **{name}:** `{' ; '.join(p)}`")
    lines += ["", "## Conclusión", "", "P0+P1 mejora la integridad de las decisiones y reduce riesgo operativo. La optimización SQLite solo se considera una mejora técnica; el benchmark sintético no prueba que la estrategia gane más dinero. Para demostrar mejora económica faltan outcomes `forward_live` netos con fills y slippage reales."]
    OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"before_ms": before, "after_ms": after, "applied_indexes": len(apply.get("applied_indexes", [])), "strict_admitted": sum(strict), "legacy_admitted": sum(legacy), "report": str(OUT)}, ensure_ascii=False))

if __name__ == "__main__": main()
