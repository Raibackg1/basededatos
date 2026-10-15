#!/usr/bin/env python3
"""
Detector de patrones de trading para memecoins de pump.fun.

Uso:
    python main.py --db mi_base.db                 # analiza tu base de datos
    python main.py --demo                          # genera datos sintéticos y los analiza
    python main.py --db mi_base.db --mapping mapping.json --tp 100 --sl 40

Salida: resultados/reporte.html, resultados/hallazgos.csv + resumen por consola.
"""
import argparse
import math
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def setup_console_encoding() -> None:
    """Configura salida UTF-8 compatible con Windows sin imports externos."""
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


setup_console_encoding()

def log(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe = (str(msg) + "\n").encode(encoding, errors="replace").decode(encoding, errors="replace")
        sys.stdout.write(safe)


def main() -> int:
    ap = argparse.ArgumentParser(description="Detector de patrones y optimizador de base de datos — pump.fun")
    ap.add_argument("--db", help="Ruta a la base de datos SQLite (.db/.sqlite)")
    ap.add_argument("--mapping", help="JSON opcional para corregir el mapeo automático de tablas/columnas")
    ap.add_argument("--tp", type=float, default=50.0, help="Take profit %% para win_tp_sl (defecto 50)")
    ap.add_argument("--sl", type=float, default=30.0, help="Stop loss %% para win_tp_sl (defecto 30)")
    ap.add_argument("--future-threshold", type=float, default=20.0,
                    help="Umbral %% de ganancia para win_fut_Nm (defecto 20)")
    ap.add_argument("--horizons", type=str, default="5,15,30", help="Horizontes futuros en minutos (defecto 5,15,30)")
    ap.add_argument("--out", default="resultados", help="Carpeta de salida")
    ap.add_argument("--demo", action="store_true", help="Genera una base sintética con patrones ocultos y la analiza")
    ap.add_argument("--optimize-db", action="store_true", help="Aplica automáticamente optimizaciones de motor SQLite (WAL, índices faltantes, pragmas)")
    ap.add_argument("--export-sql", action="store_true", default=True, help="Genera script SQL de optimización e índices en la carpeta de resultados")
    ap.add_argument("--audit-only", action="store_true", help="Audita SQLite en solo lectura y genera JSON/HTML sin ejecutar análisis de trading")
    ap.add_argument("--audit-depth", choices=("basic", "deep"), default="basic", help="Perfilado básico acotado o escaneo completo confirmado")
    ap.add_argument("--confirm-deep", action="store_true", help="Confirma explícitamente el escaneo completo de la base")
    ap.add_argument("--data-origin", choices=("unknown", "synthetic", "real"), default="unknown", help="Origen declarado de los datos; no se verifica automáticamente")
    ap.add_argument("--audit-sample-rows", type=int, default=100, help="Límite de filas de vista previa por columna en modo básico (1-10000)")
    args = ap.parse_args()

    if args.audit_only:
        if args.demo:
            ap.error("--demo regenera la base sintética y no se puede combinar con --audit-only; usa --db demo_pumpfun.db.")
        if args.optimize_db:
            ap.error("--optimize-db puede escribir en la base y no se admite con --audit-only.")
        if not args.db:
            ap.error("--audit-only requiere --db ruta.db.")
        if not 1 <= args.audit_sample_rows <= 10_000:
            ap.error("--audit-sample-rows debe estar entre 1 y 10000.")
        deep_confirmed = False
        if args.audit_depth == "deep":
            try:
                from detector.database_audit import list_user_tables
                deep_tables = list_user_tables(args.db)
            except (OSError, ValueError, TypeError) as exc:
                log(f"Error de auditoría: {exc}")
                return 2
            log("Tablas que se recorrerán en modo deep: " + ", ".join(deep_tables))
            if args.confirm_deep:
                deep_confirmed = True
            elif not sys.stdin.isatty():
                ap.error("El modo deep requiere una terminal interactiva o --confirm-deep.")
            else:
                try:
                    answer = input("El modo deep recorrerá las tablas completas y puede usar I/O/tiempo. ¿Continuar? [s/N] ")
                except EOFError:
                    answer = ""
                if answer.strip().lower() not in {"s", "si", "sí", "y", "yes"}:
                    log("Auditoría deep cancelada.")
                    return 130
                deep_confirmed = True
        try:
            from detector.audit_report import write_audit_files
            from detector.database_audit import run_database_audit

            audit = run_database_audit(
                args.db,
                depth=args.audit_depth,
                data_origin=args.data_origin,
                sample_rows=args.audit_sample_rows,
                deep_confirmed=deep_confirmed,
                mapping_path=args.mapping,
            )
            json_path, html_path = write_audit_files(audit, args.out)
        except (OSError, ValueError, TypeError) as exc:
            log(f"Error de auditoría: {exc}")
            return 2
        log("Auditoría SQLite completada en modo solo lectura.")
        log(f"JSON: {json_path}")
        log(f"HTML: {html_path}")
        return 0

    import numpy as np
    import pandas as pd

    from detector.advanced_engine import (
        detect_block0_snipers,
        detect_cabal_wallets,
        realistic_tpsl_outcome,
        setup_console_encoding,
        temporal_train_val_test_split,
    )
    from detector.config import Cfg
    from detector.data_integrity import build_provenance
    from detector.db_optimizer import DatabaseEngineOptimizer
    from detector.features import (
        POST_ENTRY_FEATURES,
        assert_no_post_entry_features,
        desc,
        token_features,
        trade_features,
    )
    from detector.inspector import apply_mapping_override, inspect_db, schema_report
    from detector.labels import build_virtual_trades, compute_trade_labels, mfe_mae_summary, tpsl_grid
    from detector.loader import load_data
    from detector.pattern_identifier import PatternIdentifier
    from detector.patterns import (confluence_rules, evaluate_confluence_oos, feature_spread,
                                   interactions, model_eval, subgroup_rules, tree_rules,
                                   univariate)
    from detector.report import LABEL_NAMES, build_report

    setup_console_encoding()

    cfg = Cfg(
        tp_pct=args.tp, sl_pct=args.sl,
        fut_threshold_pct=args.future_threshold,
        fut_horizons_min=tuple(int(x) for x in args.horizons.split(",")),
        out_dir=args.out,
    )

    db_path = args.db
    if args.demo:
        from demo_data import generate_demo_db
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_pumpfun.db")
        log("🧪 [1/7] Generando base de datos sintética de demostración...")
        generate_demo_db(db_path)
    if not db_path or not os.path.exists(db_path):
        log("❌ Indica tu base con --db ruta.db (o usa --demo para probar con datos sintéticos).")
        return 1

    log(f"\n🔎 [2/7] Inspeccionando esquema y salud del motor de {db_path} ...")
    sm = inspect_db(db_path)
    if args.mapping:
        apply_mapping_override(sm, args.mapping)
    log(schema_report(sm))

    # Optimización y Auditoría del Motor SQLite (DBMS Health & Index Advisor)
    db_optimizer = DatabaseEngineOptimizer(db_path)
    db_health = db_optimizer.analyze()
    log(f"\n⚡ [DBMS Health] Tamaño: {db_health.total_size_mb} MB | Fragmentación/Bloat: {db_health.bloat_pct}% | Modo Journal: {db_health.journal_mode}")
    if db_health.unindexed_foreign_keys:
        log(f"   ⚠️ Claves foráneas no indexadas detectadas: {len(db_health.unindexed_foreign_keys)}")
    if db_health.recommendations:
        log(f"   💡 Índices faltantes recomendados para eliminar SCAN TABLE: {len(db_health.recommendations)}")
        for rec in db_health.recommendations[:3]:
            log(f"      • {rec.index_name} en {rec.table}({', '.join(rec.columns)}) [{rec.estimated_impact}]")

    # Exportar migración SQL
    os.makedirs(cfg.out_dir, exist_ok=True)
    sql_path = os.path.join(cfg.out_dir, "optimize_db.sql")
    db_optimizer.generate_sql_migration(db_health, out_path=sql_path)
    log(f"   💾 Script de optimización SQL generado: {sql_path}")

    # Aplicar optimizaciones si se solicitó
    if args.optimize_db:
        log("   🚀 Aplicando optimizaciones de motor (PRAGMA WAL, índices recomendados)...")
        res_opt = db_optimizer.apply_optimizations(db_health)
        if res_opt.get("success"):
            log(f"   ✅ Optimizaciones aplicadas exitosamente en {res_opt.get('elapsed_ms')} ms ({len(res_opt.get('applied_indexes', []))} índices creados)")
        else:
            log(f"   ❌ Error al aplicar optimizaciones: {res_opt.get('error')}")

    log("\n📥 [3/7] Cargando y normalizando datos...")
    d = load_data(db_path, sm)
    n_tok = len(d.tokens) if d.tokens is not None else 0
    n_tr = len(d.trades) if d.trades is not None else 0
    n_my = len(d.my) if d.my is not None else 0
    log(f"   tokens={n_tok:,} · trades={n_tr:,} · posiciones propias={n_my:,} · rutas de precio={len(d.paths):,}")
    if not d.paths and n_my == 0:
        log("❌ No hay datos de precios ni trades propios que analizar. Revisa el mapeo (--mapping).")
        return 1

    log("\n🛠️ [4/7] Construyendo características de tokens y operaciones...")
    tokfeat = token_features(d, cfg)
    virtual = False
    if d.my is not None and len(d.my):
        trades_df = trade_features(d, tokfeat, cfg)
    else:
        log("   (sin tabla de trades propios → simulando entradas virtuales de sniping "
            f"a los {cfg.virtual_entry_s:.0f}s de cada token)")
        vt = build_virtual_trades(d, cfg)
        if not len(vt):
            log("❌ No se pudieron construir entradas virtuales (faltan rutas de precio).")
            return 1
        trades_df = trade_features(d, tokfeat, cfg, base_df=vt)
        virtual = True
    # Excluir del minado las columnas de RESULTADO (fuga de información):
    # solo deben usarse features conocidas en el momento de la entrada.
    cfg.exclude_features = tuple(cfg.exclude_features) + (
        "pnl_sol", "pnl_pct", "exit_price", "exit_time", "hold_s", "status",
        "win", "virtual", "graduated", "tpsl_timeout", "entry_price",
        "market_cap", "mc_at_entry_est", "fees", "raw_wallet", "raw_tx_signature",
    )
    log(f"   features por token: {tokfeat.shape[1]-1} · operaciones a analizar: {len(trades_df):,}")

    # P0: eliminar columnas cuyo valor depende de una ventana posterior a la
    # entrada. No basta con excluirlas del minado: también deben desaparecer del
    # DataFrame que alimenta el meta-labeler y la señal histórica.
    future_cols = sorted(set(trades_df.columns).intersection(POST_ENTRY_FEATURES))
    if future_cols:
        trades_df = trades_df.drop(columns=future_cols)
        log("   Anti-fuga: columnas post-entrada eliminadas: " + ", ".join(future_cols))
    assert_no_post_entry_features(trades_df.columns)

    log("\n🏷️ [5/7] Calculando etiquetas de win (real, TP/SL, futuros, graduación)...")
    trades_df = compute_trade_labels(d, trades_df, cfg)
    label_cols = []
    # win_real_net (Ola 2) tiene prioridad: mide rentabilidad ya neto de costes
    # ida+vuelta auditados (~5.64%); win_real bruto queda como referencia.
    for lc in ["win_real_net", "win_real", "win_tp_sl"] + [f"win_fut_{h}m" for h in cfg.fut_horizons_min] + ["win_grad"]:
        if lc in trades_df.columns and trades_df[lc].notna().sum() >= 20:
            label_cols.append(lc)
            s = trades_df[lc].dropna()
            log(f"   {lc:<14} n={len(s):>6,}  winrate={s.mean()*100:5.1f}%")
    if not label_cols:
        log("❌ Ninguna etiqueta alcanzó el mínimo de 20 muestras. Revisa el mapeo.")
        return 1

    main_label = next((lc for lc in ("win_tp_sl", "win_real", "win_fut_15m", "win_fut_5m",
                                      "win_fut_30m", "win_grad") if lc in label_cols), label_cols[0])

    # Guard de causalidad (ver STATUS.md §7): ninguna etiqueta debe derivarse de
    # un desenlace del ciclo de vida del token (rise50At, peak*, deadAt...). Es la
    # comprobación que habría detenido el falso "96.8%" de winrate.
    from detector.label_guard import is_lifecycle_future
    _leaky_labels = [lc for lc in label_cols if is_lifecycle_future(lc)]
    if _leaky_labels:
        log(f"   ⚠️ ETIQUETAS NO CAUSALES (lookahead): {_leaky_labels} — métricas no válidas")

    log("\n⛏️ [6/7] Minando patrones por etiqueta con control FDR y Subgroup Discovery...")
    # Partición temporal para evaluación OOS (BP-012) con purga en fronteras (Ola 1)
    train_df, val_df, test_df = temporal_train_val_test_split(
        trades_df, time_col="created_at", train_ratio=0.6, val_ratio=0.2, purge_gap=5)
    log(f"   Partición temporal OOS: Train={len(train_df):,} | Val={len(val_df):,} | Test OOS Sellado={len(test_df):,}")

    # Detección de Cabales y Snipers de Bloque 0
    cabal_info = {}
    b0_info = {}
    if d.trades is not None and len(d.trades):
        log("   Auditoría On-Chain: Analizando grafo de co-ocurrencia y snipers bloque 0...")
        cabal_info = detect_cabal_wallets(d.trades)
        b0_info = detect_block0_snipers(d.trades)
        n_cabal_wallets = len(cabal_info.get("cabal_wallets", []))
        n_b0_traps = sum(1 for v in b0_info.values() if v.get("is_block0_trap"))
        log(f"   → Wallets coludidas detectadas en cabal: {n_cabal_wallets:,} | Tokens con trampa bloque 0 (>25%): {n_b0_traps:,}")

    # P0 (procedencia): el origen de los datos es la base de honestidad de las
    # métricas. El demo SIEMPRE es 'synthetic' y nunca debe confundirse con
    # rentabilidad real. Si no se declara, queda 'unknown' (no verificado).
    data_origin = "synthetic" if args.demo else args.data_origin
    try:
        import hashlib as _hashlib
        with open(db_path, "rb") as _f:
            db_sha256 = _hashlib.sha256(_f.read()).hexdigest()[:16]
    except OSError:
        db_sha256 = None
    provenance = {
        "origin": data_origin,
        "db_file": os.path.basename(db_path),
        "db_sha256_16": db_sha256,
        "n_tokens": n_tok,
        "n_operations": int(len(trades_df)),
        "virtual_entries": bool(virtual),
    }
    provenance.update(build_provenance(db_path, data_origin, cfg))
    log(f"   Procedencia de datos: origin={data_origin} · db_sha256={db_sha256}")

    results = {
        "db_path": db_path, "schema": sm, "cfg": cfg, "labels_df": trades_df,
        "tokfeat": tokfeat, "label_cols": label_cols, "main_label": main_label,
        "univariate": {}, "rules": {}, "subgroups": {}, "models": {}, "inter": {}, "spread": {},
        "confluence": {},
        "warnings": list(d.warnings), "virtual": virtual,
        "data_origin": data_origin, "provenance": provenance,
        "cabal_info": cabal_info, "b0_info": b0_info,
        "pnl_total": float(trades_df["pnl_sol"].sum()) if "pnl_sol" in trades_df.columns else None,
        "db_health": db_health,
    }
    for lc in label_cols:
        log(f"   → {lc}")
        analysis_df = train_df if len(train_df) else trades_df
        results["univariate"][lc] = univariate(analysis_df, lc, cfg)
        results["spread"][lc] = feature_spread(results["univariate"][lc])
        results["rules"][lc] = tree_rules(analysis_df, lc, cfg)
        results["subgroups"][lc] = subgroup_rules(analysis_df, lc, cfg)
        results["models"][lc] = model_eval(analysis_df, lc, cfg)
        m = results["models"][lc]
        imp_feats = list(m["importance"].index) if m.get("ok") and len(m.get("importance", [])) else \
            results["spread"][lc].head(8)["feature"].tolist() if len(results["spread"][lc]) else []
        results["inter"][lc] = interactions(analysis_df, lc, imp_feats, cfg)
        # Ola 3: confluencias multivariantes (A ∧ B ∧ C) con control FDR.
        # Se minan SOLO en Train (60% antiguo) para evitar fuga hacia Test.
        conf_tr = confluence_rules(train_df if len(train_df) else trades_df, lc, imp_feats, cfg)
        results["confluence"][lc] = evaluate_confluence_oos(conf_tr, test_df, lc)

    # Grid Nominal y Grid Realista Auditado (BP-001, BP-010)
    from detector.advanced_engine import (
        CalibratedMetaLabeler,
        CompositeAlphaScorer,
        calculate_fractional_kelly,
        calculate_quantstats_metrics,
        evaluate_rules_oos,
        export_production_strategy,
        realistic_tpsl_grid,
        simulate_dynamic_exit,
    )
    import json

    policy_df = pd.concat([train_df, val_df], axis=0) if len(train_df) or len(val_df) else trades_df
    results["grid"] = tpsl_grid(d, policy_df, cfg)
    results["realistic_grid"] = realistic_tpsl_grid(d, policy_df, cfg)
    results["mfe_mae"] = mfe_mae_summary(trades_df)

    # -----------------------------------------------------------------------
    # Meta-Labeling: Filtrado Compuesto y Simulación de Salidas Dinámicas
    # -----------------------------------------------------------------------
    # P0: no exportar subgrupos/árboles descubiertos in-sample. Solo se pueden
    # promover confluencias entrenadas en Train y aprobadas en Test OOS.
    top_export_rules = []
    conf_main_rules = results["confluence"].get(main_label)
    if conf_main_rules is not None and len(conf_main_rules):
        oos_mask = conf_main_rules.get("oos_pass", pd.Series(False, index=conf_main_rules.index))
        promoted = conf_main_rules[conf_main_rules["fdr_sig"] & oos_mask]
        for c in promoted.head(3).to_dict("records"):
            top_export_rules.append({
                "rule": f"CONFLUENCIA: {c['confluencia']}",
                "n": c["n"], "winrate": c["winrate"], "lift": c["lift"],
                "p_value": c["p_value"], "q_value": c["q_value"],
                "fdr_sig": bool(c["fdr_sig"]),
                "oos_pass": bool(c.get("oos_pass", False)),
                "oos_lift": c.get("oos_lift"),
                "oos_winrate": c.get("oos_winrate"), "oos_n": c.get("oos_n"),
                "bounds": c.get("bounds", []),
            })

    # Enriquecer particiones temporales con cabal y block 0
    if cabal_info and "cabal_share_by_token" in cabal_info:
        for split_df in (trades_df, train_df, val_df, test_df):
            split_df["cabal_share"] = split_df["token_id"].astype(str).map(cabal_info["cabal_share_by_token"]).fillna(0.0)
    else:
        for split_df in (trades_df, train_df, val_df, test_df):
            split_df["cabal_share"] = 0.0

    if b0_info:
        for split_df in (trades_df, train_df, val_df, test_df):
            split_df["is_block0_trap"] = split_df["token_id"].astype(str).map(
                lambda tid: b0_info.get(tid, {}).get("is_block0_trap", 0)
            ).fillna(0)
    else:
        for split_df in (trades_df, train_df, val_df, test_df):
            split_df["is_block0_trap"] = 0

    # Entrenamiento y Calibración Isotónica del Meta-Labeler Secundario (BP-012)
    meta_feature_candidates = [
        "ef_vpin_30s", "ef_amihud_illiq", "ef_cpmm_kyle_lambda", "ef_order_flow_imbalance",
        "ef_unique_buyers", "ef_n_sells", "ef_curve_progress", "ef_top5_share", "cabal_share"
    ]
    avail_meta_cols = [c for c in meta_feature_candidates if c in train_df.columns]
    y_tr = train_df[main_label].fillna(0).to_numpy(int)
    y_va = val_df[main_label].fillna(0).to_numpy(int)

    meta_labeler = CalibratedMetaLabeler(target_precision=0.65, min_trades_val=10)
    meta_labeler.fit_and_calibrate(train_df, y_tr, val_df, y_va, avail_meta_cols)
    log(f"   Meta-Labeler Calibrado: tau*={meta_labeler.tau_star:.2f} | Precision Val={meta_labeler.val_metrics.get('val_precision',0)*100:.1f}%")

    from detector.master_signal import MasterSnipeSignal
    from detector.loss_learner import WinLossLearner, LossPostMortemLearner

    # 1. Aprendizaje Dual de Ganancias y Pérdidas (WinLossLearner)
    log("\n🧬 [A] Ejecutando aprendizaje dual (WinLossLearner: Ganancias y Pérdidas)...")
    win_loss_learner = WinLossLearner(trades_df)
    win_archetypes = win_loss_learner.analyze_wins()
    failure_clusters = win_loss_learner.analyze_failures()
    results["win_archetypes"] = win_archetypes
    results["failure_clusters"] = failure_clusters
    for wa in win_archetypes:
        log(f"   [+] Arquetipo {wa.name:<25} | Trades: {wa.trade_count:>4} | WR: {wa.winrate*100:4.1f}% | Retorno: +{wa.avg_gain_pct:4.1f}% | USD: +{wa.total_gain_usd:,.1f}$")
    for fc in failure_clusters:
        log(f"   [-] Cluster   {fc.name:<25} | Trades: {fc.trade_count:>4} ({fc.loss_share_pct:4.1f}% de pérdida) | Veto: {fc.recommended_veto}")

    # 2. Identificador Autónomo de Patrones y Anti-Patrones (PatternIdentifier)
    log("\n🔬 [B] Ejecutando Identificador Autónomo de Patrones y Anti-Patrones (PatternIdentifier)...")
    trades_df = PatternIdentifier.batch_identify(trades_df)
    arch_summary = trades_df["archetype_name"].value_counts()
    for arch_name, count in arch_summary.items():
        sub_arch = trades_df[trades_df["archetype_name"] == arch_name]
        wr_arch = sub_arch[main_label].mean() * 100 if main_label in sub_arch.columns and len(sub_arch) else 0.0
        log(f"   [Arquetipo] {arch_name:<28} | n={count:>4} ({count/len(trades_df)*100:4.1f}%) | WR: {wr_arch:5.1f}% | Rec: {sub_arch['recommended_action'].iloc[0]}")
    n_anti = int(trades_df["anti_patterns_count"].sum())
    log(f"   🛡️ Alertas totales de anti-patrones detectadas en transacciones: {n_anti:,}")
    results["identified_archetypes"] = arch_summary.to_dict()

    # Exportar arquetipos identificados a CSV
    arquetipos_path = os.path.join(cfg.out_dir, "arquetipos_identificados.csv")
    cols_arch = [c for c in ["token_id", "archetype_name", "archetype_category", "archetype_confidence",
                             "recommended_action", "expected_winrate", "expected_ev",
                             "anti_patterns_count", "anti_patterns_detail"] if c in trades_df.columns]
    trades_df[cols_arch].to_csv(arquetipos_path, index=False, encoding="utf-8-sig")

    # 3. Evaluación de la Señal Maestra Unificada (MasterSnipeSignal)
    log("\n⚡ [C] Evaluando Filtrador Maestro / Señal Única (MasterSnipeSignal)...")
    master_decisions = []
    for r in trades_df.to_dict("records"):
        dec = MasterSnipeSignal.evaluate(r)
        master_decisions.append(dec)

    trades_df["master_admitted"] = [d.admitted for d in master_decisions]
    trades_df["master_branch"] = [d.branch for d in master_decisions]
    trades_df["alpha_score"] = [d.confidence * 100.0 for d in master_decisions]
    trades_df["should_enter"] = [int(d.admitted) for d in master_decisions]
    trades_df["vetoed"] = [int(not d.admitted and d.branch == "VETO") for d in master_decisions]
    trades_df["veto_reason"] = [d.veto_reason or "" for d in master_decisions]

    alpha_df_master = trades_df[trades_df["should_enter"] == 1].copy()
    n_alpha_master = len(alpha_df_master)
    log(f"   Señal Maestra Unificada: {n_alpha_master:,} / {len(trades_df):,} operaciones seleccionadas ({n_alpha_master/max(len(trades_df),1)*100:.1f}%)")

    # Filtro secundario: aplicar únicamente al bloque Test OOS. Las métricas de
    # producción no deben mezclar train/validation con el resultado final.
    test_alpha_idx = alpha_df_master.index.intersection(test_df.index)
    alpha_df = alpha_df_master.loc[test_alpha_idx].copy()
    if len(alpha_df) and meta_labeler.feature_cols:
        meta_pred = meta_labeler.predict_oos(alpha_df)
        meta_mask = meta_pred["decision"].astype(bool)
        alpha_df = alpha_df.loc[meta_mask].copy()
        log(f"   Meta-Labeler OOS (tau*={meta_labeler.tau_star:.2f}) filtra Test: "
            f"{len(test_alpha_idx):,} → {len(alpha_df):,} operaciones")

    # Simulación de Salidas Dinámicas (Trailing SL + Breakeven + STALL)
    def _sim_batch_exits(sub_df, use_dynamic=True):
        wins, losses, pnl_sol_sum = 0, 0, 0.0
        gains, losses_mag = [], []
        # C4: el Profit Factor debe calcularse en USD (o SOL), no en puntos
        # porcentuales. Antes un único ganador de +150% inflaba el "PF" a dos
        # dígitos aunque la cuenta perdiera dinero.
        gross_profit_sol, gross_loss_sol = 0.0, 0.0
        r_sum = 0.0
        sl_nominal = cfg.sl_pct / 100.0
        risk_r = 0.5 * sl_nominal
        trade_records = []
        for row in sub_df.itertuples():
            tid = str(getattr(row, "token_id", ""))
            path = d.paths.get(tid)
            et, ep = getattr(row, "entry_time", np.nan), getattr(row, "entry_price", np.nan)
            
            # Simular con la ruta de ticks real si existe y es válida
            if path is not None and (et == et) and (ep == ep) and ep > 0 and len(path[0]) >= 2:
                if use_dynamic:
                    res = simulate_dynamic_exit(
                        path, et, ep,
                        initial_sl_pct=cfg.sl_pct,
                        take_profit_pct=cfg.tp_pct,
                        breakeven_trigger_pct=35.0,
                        trailing_stop_pct=20.0,
                        # C8: fuente única del timeout de estancamiento.
                        stall_window_s=MasterSnipeSignal.STALL_TIMEOUT_SECONDS,
                        realistic=True,
                        position_size_sol=0.5
                    )
                else:
                    from detector.advanced_engine import realistic_tpsl_outcome
                    res = realistic_tpsl_outcome(
                        path, et, ep, cfg.tp_pct/100.0, cfg.sl_pct/100.0,
                        cfg.mfe_window_min * 60.0, realistic=True, position_size_sol=0.5
                    )
                pnl_pct = res["pnl_pct"]
                pnl_sol = res["pnl_sol"]
            else:
                # Si no hay ticks suficientes, usar el PnL real registrado
                raw_pct = getattr(row, "pnl_pct", 0.0)
                try:
                    pnl_pct = float(raw_pct) if raw_pct is not None else 0.0
                    if math.isnan(pnl_pct) or math.isinf(pnl_pct):
                        pnl_pct = 0.0
                except (ValueError, TypeError):
                    pnl_pct = 0.0

                raw_sol = getattr(row, "pnl_sol", 0.0)
                try:
                    pnl_sol = float(raw_sol) if raw_sol is not None else 0.0
                    if math.isnan(pnl_sol) or math.isinf(pnl_sol):
                        pnl_sol = 0.0
                except (ValueError, TypeError):
                    pnl_sol = 0.0

                if pnl_sol == 0.0 and pnl_pct != 0.0:
                    pnl_sol = 0.5 * (pnl_pct / 100.0)

            pnl_sol_sum += pnl_sol
            r_sum += pnl_sol / max(risk_r, 1e-6)
            trade_records.append({"tid": tid, "pnl_sol": pnl_sol, "pnl_pct": pnl_pct})
            if pnl_sol > 0:
                gross_profit_sol += pnl_sol
            elif pnl_sol < 0:
                gross_loss_sol += -pnl_sol
            if pnl_pct > 0:
                wins += 1
                gains.append(pnl_pct)
            else:
                losses += 1
                losses_mag.append(abs(pnl_pct))
        n_tot = wins + losses
        wr = wins / n_tot if n_tot else 0.0
        ev_r = r_sum / n_tot if n_tot else 0.0
        # PF en USD (C4): ganancia bruta / pérdida bruta sobre pnl_sol.
        pf = (gross_profit_sol / gross_loss_sol) if gross_loss_sol > 0 else \
            (float("inf") if gross_profit_sol > 0 else 0.0)
        pf = 999.0 if pf == float("inf") else pf
        avg_w = float(np.mean(gains)) if gains else 0.0
        avg_l = float(np.mean(losses_mag)) if losses_mag else 1.0
        return {
            "n": n_tot, "winrate": wr, "expectancy_r": ev_r, "profit_factor": pf,
            "profit_factor_basis": "usd",
            "gross_profit_sol": gross_profit_sol, "gross_loss_sol": gross_loss_sol,
            "pnl_sol": pnl_sol_sum, "avg_win": avg_w, "avg_loss": avg_l,
            "records": trade_records
        }

    m_unfiltered_static = _sim_batch_exits(trades_df, use_dynamic=False)
    m_unfiltered_dyn = _sim_batch_exits(trades_df, use_dynamic=True)
    m_alpha_master_dyn = _sim_batch_exits(alpha_df_master, use_dynamic=True)
    m_alpha_dyn = _sim_batch_exits(alpha_df, use_dynamic=True)

    # Cálculo de Kelly Fraccionario
    kelly_f = calculate_fractional_kelly(
        winrate=m_alpha_dyn["winrate"],
        avg_win_pct=m_alpha_dyn["avg_win"],
        avg_loss_pct=m_alpha_dyn["avg_loss"],
        fraction=0.20,
        max_cap=0.10,
    )

    # Métricas de QuantStats y Curva de Capital
    pnl_unfiltered_series = pd.Series([r["pnl_sol"] for r in m_unfiltered_dyn["records"]])
    pnl_alpha_series = pd.Series([r["pnl_sol"] for r in m_alpha_dyn["records"]])

    qs_unfiltered = calculate_quantstats_metrics(pnl_unfiltered_series, capital_initial_sol=10.0, r_unit_sol=0.5)
    qs_alpha = calculate_quantstats_metrics(pnl_alpha_series, capital_initial_sol=10.0, r_unit_sol=0.5)

    kelly_eq = [10.0]
    for p in pnl_alpha_series:
        trade_size = kelly_eq[-1] * kelly_f
        scaled_pnl = p * (trade_size / 0.5)
        kelly_eq.append(max(0.01, kelly_eq[-1] + scaled_pnl))

    results["quantstats"] = qs_alpha
    results["equity_comparison"] = {
        "baseline": qs_unfiltered.get("equity_curve", []),
        "alpha": qs_alpha.get("equity_curve", []),
        "kelly": kelly_eq,
    }

    # Datos para simulador interactivo en HTML
    sim_records = []
    pnl_by_tid = {r["tid"]: r["pnl_sol"] for r in m_unfiltered_dyn["records"]}
    for row in trades_df.itertuples():
        tid = str(getattr(row, "token_id", ""))
        p_sol = pnl_by_tid.get(tid, 0.0)
        sim_records.append({
            "score": round(float(getattr(row, "alpha_score", 0.0)) / 100.0, 2),
            "vetoed": int(getattr(row, "vetoed", 0)),
            "pnl": round(float(p_sol), 4),
        })
    results["sim_data"] = {"records_json": json.dumps(sim_records)}

    log("\n📝 [7/7] Generando reporte y artefactos de producción...")
    os.makedirs(cfg.out_dir, exist_ok=True)
    report_path = build_report(results, cfg.out_dir)

    # hallazgos.csv con subgrupos, reglas, univariado, FDR q-values y evaluación OOS
    hall = []
    for lc in label_cols:
        sg = results["subgroups"].get(lc)
        if sg is not None and len(sg):
            sgg = sg.copy()
            sgg["tipo"] = "subgrupo_wracc"
            sgg = evaluate_rules_oos(sgg, test_df, lc)
            hall.append(sgg)
        r = results["rules"].get(lc)
        if r is not None and len(r):
            rr = r.copy()
            rr["tipo"] = "arbol"
            rr = evaluate_rules_oos(rr, test_df, lc)
            hall.append(rr)
        u = results["univariate"].get(lc)
        if u is not None and len(u):
            uu = u.rename(columns={"bin": "rule"}).copy()
            uu["tipo"] = "univariado"
            uu["label"] = lc
            hall.append(uu)
        cf = results["confluence"].get(lc)
        if cf is not None and len(cf):
            cff = cf.rename(columns={"confluencia": "rule"}).copy()
            cff["tipo"] = "confluencia"
            cff["label"] = lc
            hall.append(cff)

    if hall:
        hall_df = pd.concat(hall, ignore_index=True)
        cols_to_save = [c for c in ["tipo", "label", "rule", "n", "wins", "winrate", "baseline",
                                    "lift", "wracc", "p_value", "q_value", "fdr_sig", "oos_n",
                                    "oos_winrate", "oos_lift", "oos_pass"] if c in hall_df.columns]
        hall_df[cols_to_save].to_csv(
            os.path.join(cfg.out_dir, "hallazgos.csv"), index=False, encoding="utf-8-sig")

    # Exportar estrategia ejecutable para producción
    best_policy = (cfg.tp_pct, cfg.sl_pct)
    if results.get("realistic_grid", {}).get("realistic_expectancy"):
        real_ex = results["realistic_grid"]["realistic_expectancy"]
        if real_ex:
            best_policy = max(real_ex, key=lambda k: real_ex[k])

    exported = export_production_strategy(top_export_rules, best_policy, cfg.out_dir)

    # Resumen final por consola
    log("\n" + "=" * 78)
    log("RESUMEN DE AUDITORÍA Y MAXIMIZACIÓN CUANTITATIVA (WINRATE & EV)")
    log("=" * 78)
    log("🔹 Comparativa de Rendimiento (Fricciones Reales: Slippage -33.49% y Fees 0.002 SOL):")
    log(f"  [1. BASELINE SIN FILTRO]    n={m_unfiltered_static['n']:>4} | WR={m_unfiltered_static['winrate']*100:5.1f}% | EV={m_unfiltered_static['expectancy_r']:+5.2f}R | PF={m_unfiltered_static['profit_factor']:4.2f} | PnL={m_unfiltered_static['pnl_sol']:+6.2f} SOL")
    log(f"  [2. + SALIDAS DINÁMICAS]    n={m_unfiltered_dyn['n']:>4} | WR={m_unfiltered_dyn['winrate']*100:5.1f}% | EV={m_unfiltered_dyn['expectancy_r']:+5.2f}R | PF={m_unfiltered_dyn['profit_factor']:4.2f} | PnL={m_unfiltered_dyn['pnl_sol']:+6.2f} SOL")
    log(f"  [3. + SEÑAL MAESTRA ALFA]   n={m_alpha_master_dyn['n']:>4} | WR={m_alpha_master_dyn['winrate']*100:5.1f}% | EV={m_alpha_master_dyn['expectancy_r']:+5.2f}R | PF={m_alpha_master_dyn['profit_factor']:4.2f} | PnL={m_alpha_master_dyn['pnl_sol']:+6.2f} SOL")
    log(f"  [4. + META-LABELER tau*]    n={m_alpha_dyn['n']:>4} | WR={m_alpha_dyn['winrate']*100:5.1f}% | EV={m_alpha_dyn['expectancy_r']:+5.2f}R | PF={m_alpha_dyn['profit_factor']:4.2f} | PnL={m_alpha_dyn['pnl_sol']:+6.2f} SOL")

    # Desglose temporal aislado por bloque (BP-012)
    train_alpha_idx = alpha_df_master.index.intersection(train_df.index)
    val_alpha_idx = alpha_df_master.index.intersection(val_df.index)
    test_alpha_idx = alpha_df_master.index.intersection(test_df.index)
    m_train_master = _sim_batch_exits(alpha_df_master.loc[train_alpha_idx], use_dynamic=True)
    m_val_master = _sim_batch_exits(alpha_df_master.loc[val_alpha_idx], use_dynamic=True)
    m_test_master = _sim_batch_exits(alpha_df_master.loc[test_alpha_idx], use_dynamic=True)

    log("\n🔹 Desglose por Partición Temporal Estricta (Aislamiento OOS BP-012):")
    log(f"  [Train 60%  - Descubrimiento] n={m_train_master['n']:>4} | WR={m_train_master['winrate']*100:5.1f}% | EV={m_train_master['expectancy_r']:+5.2f}R | PF={m_train_master['profit_factor']:4.2f} | PnL={m_train_master['pnl_sol']:+6.2f} SOL")
    log(f"  [Val 20%    - Calibración]   n={m_val_master['n']:>4} | WR={m_val_master['winrate']*100:5.1f}% | EV={m_val_master['expectancy_r']:+5.2f}R | PF={m_val_master['profit_factor']:4.2f} | PnL={m_val_master['pnl_sol']:+6.2f} SOL")
    log(f"  [Test 20%   - OOS Sellado]   n={m_test_master['n']:>4} | WR={m_test_master['winrate']*100:5.1f}% | EV={m_test_master['expectancy_r']:+5.2f}R | PF={m_test_master['profit_factor']:4.2f} | PnL={m_test_master['pnl_sol']:+6.2f} SOL")
    log(f"  [Test 20%   - + Meta-Labeler] n={m_alpha_dyn['n']:>4} | WR={m_alpha_dyn['winrate']*100:5.1f}% | EV={m_alpha_dyn['expectancy_r']:+5.2f}R | PF={m_alpha_dyn['profit_factor']:4.2f} | PnL={m_alpha_dyn['pnl_sol']:+6.2f} SOL")

    # Auditoría de costes reales (Ola 2): WR neto y breakeven exigido
    from detector.costs import DEFAULT_COSTS
    if m_alpha_dyn["n"] > 0:
        wr_net = m_alpha_dyn["winrate"] * DEFAULT_COSTS.net_pnl_pct(m_alpha_dyn["avg_win"]) + \
            (1.0 - m_alpha_dyn["winrate"]) * DEFAULT_COSTS.net_pnl_pct(-m_alpha_dyn["avg_loss"])
        be_wr = DEFAULT_COSTS.breakeven_winrate(
            DEFAULT_COSTS.net_pnl_pct(m_alpha_dyn["avg_win"]),
            DEFAULT_COSTS.net_pnl_pct(-m_alpha_dyn["avg_loss"]),
        )
        log(f"  [Costes reales] roundtrip={DEFAULT_COSTS.roundtrip_frac*100:.2f}% | "
            f"EV unitaria neta={wr_net:+.2f}% | WR breakeven exigido="
            f"{be_wr*100:.1f}%" if be_wr == be_wr else "N/A")

    log("\n🔹 Analítica Financiera Institucional (QuantStats / PyFolio en Señal Maestra Alfa):")
    log(f"  • Sortino Ratio (Anualizado):        {qs_alpha.get('sortino_ratio', 0.0):6.2f}")
    log(f"  • Calmar Ratio (CAGR/MDD):          {qs_alpha.get('calmar_ratio', 0.0):6.2f}")
    log(f"  • Max Drawdown:                     {qs_alpha.get('max_drawdown_pct', 0.0):6.2f}%")
    log(f"  • Max Drawdown Duration:            {qs_alpha.get('max_drawdown_duration_trades', 0):>6} operaciones")
    log(f"  • Cornish-Fisher VaR (95%):         {qs_alpha.get('cornish_fisher_var95_pct', 0.0):6.2f}%")
    log(f"  • Capital Final (10 SOL base):       {qs_alpha.get('final_equity_sol', 10.0):6.2f} SOL (Retorno: {qs_alpha.get('total_return_pct', 0.0):+.1f}%)")

    log("\n🔹 Arquetipos de Ganancia Aprendidos (Win Archetypes / Winners DNA):")
    for wa in win_archetypes:
        log(f"  [+] {wa.name:<25} | {wa.trade_count:>4} trades (WR: {wa.winrate*100:4.1f}%) | Retorno: +{wa.avg_gain_pct:4.1f}% | USD: +{wa.total_gain_usd:,.1f}$")

    log("\n🔹 Descomposición Forense de Pérdidas (Loss Clusters & Shields):")
    for fc in failure_clusters:
        log(f"  [-] {fc.name:<25} | {fc.trade_count:>4} trades ({fc.loss_share_pct:4.1f}% de pérdida) | Veto: {fc.recommended_veto}")

    log("\n🔹 Dimensionamiento de Posición (Fractional Kelly Criterion):")
    log(f"  Tamaño óptimo recomendado por token: {kelly_f*100:.1f}% de cartera (Quarter-Kelly κ=0.20)")

    log("\n🔹 Reglas de Oro con Validación FDR (q <= 0.05 / Subgrupos WRAcc):")
    if top_export_rules:
        for t in top_export_rules[:5]:
            q_val = t.get("q_value")
            q_str = f"q={q_val:.2g}" if q_val is not None and np.isfinite(q_val) else f"p={t.get('p_value', 1.0):.2g}"
            log(f"  [+] lift {t.get('lift', 1):.2f}x · WR {t.get('winrate', 0)*100:.1f}% · n={t.get('n', 0)} · {q_str} · SI {t.get('rule')}")

    log("\n🔹 Confluencias Multivariantes A ∧ B ∧ C (Ola 3, minadas en Train, eval en Test OOS):")
    conf_main = results["confluence"].get(main_label)
    if conf_main is not None and len(conf_main):
        for c in conf_main.head(5).itertuples():
            sig = "★FDR" if getattr(c, "fdr_sig", False) else f"q={c.q_value:.2g}"
            oos = getattr(c, "oos_winrate", np.nan)
            oos_str = f" · OOS WR {oos*100:.1f}% (n={getattr(c, 'oos_n', 0)})" if oos == oos else " · OOS n/d"
            log(f"  [∧] lift {c.lift:.2f}x · WR {c.winrate*100:.1f}% · n={c.n} · depth={c.depth} · {sig}{oos_str} · {c.confluencia}")
    else:
        log("  (sin confluencias significativas con las features disponibles)")

    log(f"\n  📄 Reporte interactivo HTML:        {report_path}")
    log(f"  📄 Hallazgos completos CSV:         {os.path.join(cfg.out_dir, 'hallazgos.csv')}")
    log(f"  🧬 Arquetipos Identificados CSV:    {arquetipos_path}")
    log(f"  ⚡ Script Optimización Motor SQL:   {sql_path}")
    log(f"  📦 Reglas Producción JSON:          {exported['json_path']}")
    log(f"  🤖 Código Estrategia Python:        {exported['py_path']}")

    log("\n🔹 Rendimiento Físico y Optimización de Base de Datos (DBMS Advisor):")
    log(f"  • Tamaño de BD:                     {db_health.total_size_mb} MB (Bloat: {db_health.bloat_pct}%)")
    log(f"  • Modo Journal actual -> sugerido:  {db_health.journal_mode} -> {db_health.optimal_pragmas['journal_mode']['recommended']}")
    log(f"  • Índices recomendados generados:   {len(db_health.recommendations)} (en {sql_path})")
    if db_health.unindexed_foreign_keys:
        log(f"  • Claves foráneas no indexadas:     {len(db_health.unindexed_foreign_keys)}")

    log("\n🔹 Clasificación de Arquetipos Identificados (PatternIdentifier):")
    for a_name, a_cnt in arch_summary.items():
        log(f"  • {a_name:<28} n={a_cnt:>4} ({a_cnt/len(trades_df)*100:4.1f}%)")
    log(f"  • Alertas totales de anti-patrones: {n_anti:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
