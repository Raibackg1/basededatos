"""Generación del reporte HTML autocontenido (CSS inline + gráficos en base64)."""
import base64
import io
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .features import desc
from .inspector import schema_report

plt.rcParams.update({
    "figure.dpi": 110, "savefig.bbox": "tight",
    "font.size": 9, "axes.grid": True, "grid.alpha": 0.3,
})

LABEL_NAMES = {
    "win_real": "Win real (PnL > 0)",
    "win_tp_sl": "Win TP/SL (ruta de precio)",
    "win_grad": "Win graduación (Raydium)",
}


def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _img(b64: str, width="100%", alt: str = "Gráfico del análisis") -> str:
    return (f'<img src="{b64}" alt="{alt}" '
            f'style="width:{width};max-width:860px;border-radius:8px;"/>')


def _df_html(df: pd.DataFrame, max_rows=40, caption: str = "") -> str:
    if df is None or not len(df):
        return "<p class='muted'>Sin datos suficientes.</p>"
    d = df.head(max_rows).copy()
    for c in d.columns:
        if d[c].dtype.kind == "f":
            d[c] = d[c].map(lambda v: f"{v:.4g}" if pd.notna(v) else "—")
    html = d.to_html(index=False, escape=True, border=0, classes="tbl")
    # A11y: th con scope="col" y caption accesible (WCAG 2.2 AA)
    html = html.replace("<th>", '<th scope="col">')
    if caption:
        html = html.replace("<table ", f'<table ', 1)
        html = html.replace(">", f"><caption>{caption}</caption>", 1)
    if len(df) > max_rows:
        html += f"<p class='muted'>… {len(df)-max_rows} filas más en hallazgos.csv</p>"
    return html


# ---------------------------------------------------------------------------
# Gráficos
# ---------------------------------------------------------------------------
def chart_winrates(results: dict) -> str:
    df = results["labels_df"]
    labels = results["label_cols"]
    names, wrs, ns = [], [], []
    for lc in labels:
        s = df[lc].dropna()
        if len(s) < 10:
            continue
        names.append(LABEL_NAMES.get(lc, lc.replace("win_", "")))
        wrs.append(s.mean() * 100)
        ns.append(len(s))
    if not names:
        return ""
    fig, ax = plt.subplots(figsize=(7.5, 3.2))
    bars = ax.bar(names, wrs, color=["#2563eb", "#16a34a", "#d97706", "#7c3aed", "#dc2626"][: len(names)])
    for b, w, n in zip(bars, wrs, ns):
        ax.text(b.get_x() + b.get_width() / 2, w + 0.6, f"{w:.1f}%\n(n={n})", ha="center", fontsize=8)
    ax.set_ylabel("Winrate %")
    ax.set_ylim(0, max(wrs) * 1.25 + 5)
    ax.set_title("Winrate por definición de 'victoria'")
    plt.setp(ax.get_xticklabels(), rotation=12, ha="right")
    return _img(_fig_to_b64(fig), alt="Gráfico de barras: winrate por definición de victoria")


def chart_grid(results: dict) -> str:
    grid = results.get("grid")
    if not grid or not grid.get("winrate"):
        return ""
    tps, sls = grid["tps"], grid["sls"]
    wr = np.array([[grid["winrate"].get((tp, sl), np.nan) for sl in sls] for tp in tps]) * 100
    ex = np.array([[grid["expectancy"].get((tp, sl), np.nan) for sl in sls] for tp in tps])
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.8))
    for ax, mat, title, cmap, fmt in (
        (axes[0], wr, "Winrate % por TP (filas) x SL (cols)", "Blues", "{:.0f}"),
        (axes[1], ex, "Expectancy (R) por TP x SL", "RdYlGn", "{:.2f}"),
    ):
        im = ax.imshow(mat, cmap=cmap, aspect="auto")
        ax.set_xticks(range(len(sls)), [f"-{s}%" for s in sls])
        ax.set_yticks(range(len(tps)), [f"+{t}%" for t in tps])
        for i in range(len(tps)):
            for j in range(len(sls)):
                if np.isfinite(mat[i, j]):
                    ax.text(j, i, fmt.format(mat[i, j]), ha="center", va="center", fontsize=6.5)
        ax.set_title(title, fontsize=9)
        fig.colorbar(im, ax=ax, shrink=0.8)
    return _img(_fig_to_b64(fig), "100%", alt="Mapa de calor: winrate y expectancy por matriz TP x SL")


def chart_uni_top(results: dict, label: str, top: int = 8) -> str:
    uni = results["univariate"].get(label)
    spread = results["spread"].get(label)
    if uni is None or not len(uni) or spread is None or not len(spread):
        return ""
    feats = spread.head(top)["feature"].tolist()
    fig, axes = plt.subplots(2, 4, figsize=(13.5, 5.6))
    base = uni["baseline"].iloc[0] * 100
    for ax, f in zip(axes.ravel(), feats + [None] * (8 - len(feats))):
        if f is None:
            ax.axis("off")
            continue
        sub = uni[uni["feature"] == f].copy()
        # ordenar bins de forma natural
        sub = sub.iloc[::-1] if sub["winrate"].iloc[0] > sub["winrate"].iloc[-1] else sub
        ax.barh(range(len(sub)), sub["winrate"] * 100, color="#2563eb", alpha=0.85)
        ax.axvline(base, color="#dc2626", ls="--", lw=1)
        ax.set_yticks(range(len(sub)), sub["bin"].astype(str), fontsize=6.5)
        ax.set_title(desc(f)[:34], fontsize=8)
        ax.set_xlabel("winrate %", fontsize=7)
        for i, (w, n) in enumerate(zip(sub["winrate"] * 100, sub["n"])):
            ax.text(w + 0.4, i, f"{w:.0f}% (n={n})", va="center", fontsize=6)
    fig.suptitle(f"Winrate por nivel de característica — etiqueta: {LABEL_NAMES.get(label, label)}", fontsize=10)
    fig.tight_layout()
    return _img(_fig_to_b64(fig), "100%", alt="Gráficos de barras: winrate por nivel de cada característica")


def chart_importance(results: dict) -> str:
    models = results["models"]
    ok = {k: v for k, v in models.items() if v.get("ok") and len(v.get("importance", []))}
    if not ok:
        return ""
    fig, axes = plt.subplots(1, len(ok), figsize=(5.2 * len(ok), 4.2))
    if len(ok) == 1:
        axes = [axes]
    for ax, (lc, m) in zip(axes, ok.items()):
        imp = m["importance"].head(12)[::-1]
        ax.barh([desc(i)[:30] for i in imp.index], imp.values, color="#16a34a", alpha=0.85)
        ax.set_title(f"Importancia — {LABEL_NAMES.get(lc, lc)}\nAUC={m['auc']:.3f}", fontsize=9)
    fig.tight_layout()
    return _img(_fig_to_b64(fig), "100%", alt="Gráfico de barras: importancia de características del modelo")


def chart_roc(results: dict) -> str:
    from sklearn.metrics import roc_curve
    models = results["models"]
    ok = {k: v for k, v in models.items() if v.get("ok")}
    if not ok:
        return ""
    fig, ax = plt.subplots(figsize=(4.6, 4.2))
    for lc, m in ok.items():
        fpr, tpr, _ = roc_curve(m["y"], m["proba"])
        ax.plot(fpr, tpr, lw=1.8, label=f"{LABEL_NAMES.get(lc, lc)} (AUC={m['auc']:.3f})")
    ax.plot([0, 1], [0, 1], "k--", lw=0.8)
    ax.set_xlabel("FPR"); ax.set_ylabel("TPR"); ax.set_title("Curvas ROC (out-of-fold)")
    ax.legend(fontsize=7)
    return _img(_fig_to_b64(fig), "60%", alt="Curvas ROC out-of-fold por etiqueta")


def chart_mfe_mae(results: dict) -> str:
    df = results["labels_df"]
    if "mfe_pct" not in df.columns or df["mfe_pct"].notna().sum() < 10:
        return ""
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.4))
    for ax, col, color, title in (
        (axes[0], "mfe_pct", "#16a34a", "MFE: máx. ganancia no realizada (%)"),
        (axes[1], "mae_pct", "#dc2626", "MAE: máx. pérdida no realizada (%)"),
    ):
        s = df[col].dropna().clip(-100, 500)
        ax.hist(s, bins=40, color=color, alpha=0.8)
        med = float(np.median(s))
        ax.axvline(med, color="k", ls="--", lw=1)
        ax.set_title(f"{title}\nmediana={med:.0f}%", fontsize=9)
    fig.tight_layout()
    return _img(_fig_to_b64(fig), "100%", alt="Histogramas de distribución de excursiones MFE y MAE")


def chart_equity_comparison(results: dict) -> str:
    eq_data = results.get("equity_comparison")
    if not eq_data:
        return ""
    base_eq = eq_data.get("baseline", [])
    alpha_eq = eq_data.get("alpha", [])
    kelly_eq = eq_data.get("kelly", [])
    if not len(base_eq) and not len(alpha_eq):
        return ""
    fig, ax = plt.subplots(figsize=(10.5, 4.0))
    if len(base_eq):
        ax.plot(base_eq, label="Baseline (Sin Filtro)", color="#94a3b8", lw=1.5, ls="--")
    if len(alpha_eq):
        ax.plot(alpha_eq, label="Alpha Strategy (Filtro Calibrado)", color="#10b981", lw=2.2)
    if len(kelly_eq):
        ax.plot(kelly_eq, label="Compound Kelly (κ=0.20)", color="#38bdf8", lw=2.0)
    ax.set_title("Evolución de Capital Acumulado (SOL) — Evaluación Fuera de Muestra (OOS)", fontsize=10)
    ax.set_xlabel("Número de Operaciones", fontsize=8)
    ax.set_ylabel("Capital (SOL)", fontsize=8)
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    return _img(_fig_to_b64(fig), "100%", alt="Curva de capital acumulado OOS: baseline vs alpha strategy vs Kelly")


def build_interactive_simulator(results: dict) -> str:
    sim = results.get("sim_data", {})
    records_json = sim.get("records_json", "[]")
    if not records_json or records_json == "[]":
        return ""
    return f"""
    <div class='card' style='margin-top:24px;padding:20px;border:1px solid #38bdf8;background:#131d33;'>
      <h3 style='color:#38bdf8;margin-top:0;'>⚡ Simulador Interactivo de Umbral Calibrado en Tiempo Real</h3>
      <p class='muted'>Mueve el deslizador para simular en vivo el impacto del umbral de decisión en el Winrate y EV:</p>
      <div style='display:flex;align-items:center;gap:15px;margin:15px 0;'>
        <label for='tauSlider' style='font-size:14px;color:#cbd5e1;'>Umbral de Decisión &tau;: <b id='tauDisplay' style='color:#38bdf8;'>0.65</b></label>
        <input type='range' id='tauSlider' min='40' max='85' value='65' step='1' style='flex:1;cursor:pointer;'>
      </div>
      <div class='cards'>
        <div class='card'><div class='v' id='simWr' style='color:#10b981;'>--</div><div class='l'>Winrate Simulado</div></div>
        <div class='card'><div class='v' id='simTrades'>--</div><div class='l'>Trades Admitidos</div></div>
        <div class='card'><div class='v' id='simPf' style='color:#38bdf8;'>--</div><div class='l'>Profit Factor</div></div>
        <div class='card'><div class='v' id='simEv' style='color:#a855f7;'>--</div><div class='l'>Expectancy (R)</div></div>
      </div>
    </div>
    <script>
    (function() {{
      const trades = {records_json};
      const slider = document.getElementById('tauSlider');
      const disp = document.getElementById('tauDisplay');
      const elWr = document.getElementById('simWr');
      const elTrades = document.getElementById('simTrades');
      const elPf = document.getElementById('simPf');
      const elEv = document.getElementById('simEv');
      function update() {{
        if (!slider) return;
        const tau = parseFloat(slider.value) / 100.0;
        disp.textContent = tau.toFixed(2);
        const sel = trades.filter(t => t.score >= tau && t.vetoed === 0);
        const n = sel.length;
        elTrades.textContent = n;
        if (n === 0) {{
          elWr.textContent = '0.0%'; elPf.textContent = '0.00'; elEv.textContent = '+0.00R';
          return;
        }}
        const wins = sel.filter(t => t.pnl > 0).length;
        const wr = (wins / n) * 100.0;
        const totWin = sel.filter(t => t.pnl > 0).reduce((a, b) => a + b.pnl, 0);
        const totLoss = Math.abs(sel.filter(t => t.pnl < 0).reduce((a, b) => a + b.pnl, 0));
        const pf = totLoss > 0 ? (totWin / totLoss).toFixed(2) : (totWin > 0 ? '99.0' : '0.00');
        const ev = (sel.reduce((a, b) => a + b.pnl, 0) / (n * 0.5)).toFixed(2);
        elWr.textContent = wr.toFixed(1) + '%';
        elPf.textContent = pf;
        elEv.textContent = (ev >= 0 ? '+' : '') + ev + 'R';
      }}
      if (slider) {{ slider.addEventListener('input', update); update(); }}
    }})();
    </script>
    """


# ---------------------------------------------------------------------------
# Recomendaciones automáticas
# ---------------------------------------------------------------------------
def build_recommendations(results: dict) -> list:
    recs = []
    df = results["labels_df"]
    cfg = results["cfg"]

    # 1) Política de salidas desde el grid
    grid = results.get("grid")
    if grid and grid.get("expectancy"):
        ex = grid["expectancy"]
        best_ex = max(ex, key=lambda k: ex[k])
        wr = grid["winrate"][best_ex]
        recs.append((
            "salida",
            f"🎯 <b>Salidas óptimas según tus rutas de precio:</b> TP <b>+{best_ex[0]}%</b> / SL <b>-{best_ex[1]}%</b> "
            f"maximiza la expectancy ({ex[best_ex]:+.2f}R por trade, winrate {wr*100:.0f}%) "
            f"frente a tu configuración actual (+{cfg.tp_pct:.0f}%/-{cfg.sl_pct:.0f}%). "
            "Nota: winrate y expectancy tiran en direcciones opuestas; prioriza expectancy a menos que tu psicología necesite más aciertos.",
        ))
    mm = results.get("mfe_mae", {})
    if "mfe_pct" in mm and "mae_pct" in mm:
        med_mfe, med_mae = mm["mfe_pct"][50], mm["mae_pct"][50]
        recs.append((
            "salida",
            f"📐 <b>Dimensiona TP/SL con tus excursiones reales:</b> el trade mediano llega a "
            f"<b>+{med_mfe:.0f}%</b> (MFE) y sufre <b>{med_mae:.0f}%</b> (MAE) antes de cerrarse. "
            "Un TP muy por encima del MFE mediano casi nunca se toca; un SL más ajustado que el MAE mediano "
            "te saca de trades que luego ganan.",
        ))

    # 2) Reglas de entrada (label principal)
    main_label = results["main_label"]
    rules = results["rules"].get(main_label)
    if rules is not None and len(rules):
        sig = rules[rules["p_value"] < cfg.pvalue_threshold]
        pos = sig[sig["lift"] >= cfg.lift_min].head(3)
        neg = sig[sig["lift"] <= 1 / cfg.lift_min].head(3)
        for r in pos.itertuples():
            recs.append((
                "entrada",
                f"✅ <b>PRIORIZA</b> snipes cuando: <b>{r.rule}</b> → winrate "
                f"{r.winrate*100:.0f}% vs {r.baseline*100:.0f}% base (lift {r.lift:.2f}x, n={r.n}, p={r.p_value:.3g}).",
            ))
        for r in neg.itertuples():
            recs.append((
                "entrada",
                f"⛔ <b>EVITA / filtra</b> cuando: <b>{r.rule}</b> → winrate "
                f"{r.winrate*100:.0f}% vs {r.baseline*100:.0f}% base (lift {r.lift:.2f}x, n={r.n}, p={r.p_value:.3g}).",
            ))
        if not len(pos) and not len(neg):
            recs.append(("entrada", "⚠️ Ninguna regla de entrada supera el umbral de significancia; revisa 'hallazgos.csv' para señales más débiles."))

    # 3) Retraso de entrada
    uni = results["univariate"].get(main_label)
    if uni is not None and len(uni):
        ed = uni[uni["feature"] == "entry_delay_s"]
        if len(ed) >= 2:
            best_bin = ed.loc[ed["winrate"].idxmax()]
            worst_bin = ed.loc[ed["winrate"].idxmin()]
            if best_bin["winrate"] - worst_bin["winrate"] > 0.08:
                recs.append((
                    "timing",
                    f"⏱️ <b>Timing de entrada:</b> tu winrate varía de "
                    f"{worst_bin['winrate']*100:.0f}% (retraso {worst_bin['bin']}s) a "
                    f"{best_bin['winrate']*100:.0f}% (retraso {best_bin['bin']}s). "
                    "Entrar en el primer segundo no siempre es mejor: los snipers ultra-tempranos "
                    "suelen ser la liquidez de salida del dev y de otros snipers.",
                ))
    # 4) Predictibilidad
    m = results["models"].get(main_label, {})
    if m.get("ok"):
        auc = m["auc"]
        txt = (
            f"🤖 <b>¿Cuánta señal hay?</b> El modelo out-of-fold alcanza AUC={auc:.3f} para '{LABEL_NAMES.get(main_label, main_label)}'. "
            + ("Hay estructura explotable: filtrar operaciones con este modelo debería mejorar tu winrate."
               if auc >= 0.6 else
               "La predictibilidad es baja con estas features: gana más mejorando la selección de tokens (datos on-chain, socials, calidad del dev) y la gestión de salidas que afinando la entrada.")
        )
        recs.append(("modelo", txt))
    # 5) Tamaño de muestra
    n = int(df[main_label].notna().sum())
    if n < 200:
        recs.append(("meta", f"📊 <b>Muestra pequeña (n={n}).</b> Los patrones pueden ser ruido: re-ejecuta el detector cuando acumules ≥300 operaciones cerradas."))
    return recs


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
CSS = """
body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;margin:0;background:#0f172a;color:#e2e8f0;}
.wrap{max-width:1000px;margin:0 auto;padding:24px;}
h1{font-size:26px;margin:8px 0 2px;} h2{font-size:19px;margin-top:36px;border-bottom:1px solid #334155;padding-bottom:6px;color:#7dd3fc;}
h3{font-size:15px;color:#a5b4fc;margin-bottom:4px;}
.muted{color:#94a3b8;font-size:12.5px;}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin:18px 0;}
.card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:14px 18px;min-width:150px;flex:1;}
.card .v{font-size:24px;font-weight:700;color:#7dd3fc;} .card .l{font-size:12px;color:#94a3b8;margin-top:2px;}
.tbl{border-collapse:collapse;width:100%;font-size:12.5px;margin:8px 0;background:#1e293b;border-radius:8px;overflow:hidden;}
.tbl th{background:#334155;color:#e2e8f0;padding:6px 8px;text-align:left;font-weight:600;}
.tbl td{padding:5px 8px;border-top:1px solid #2b3a52;color:#cbd5e1;}
.tbl tr:hover td{background:#24314a;}
.rec{background:#1e293b;border-left:4px solid #16a34a;border-radius:8px;padding:10px 14px;margin:10px 0;font-size:13.5px;line-height:1.5;}
.rec.salida{border-color:#d97706;} .rec.entrada{border-color:#2563eb;} .rec.timing{border-color:#7c3aed;} .rec.modelo{border-color:#0891b2;} .rec.meta{border-color:#64748b;}
.badge{display:inline-block;background:#334155;border-radius:20px;padding:2px 10px;font-size:11px;color:#94a3b8;margin:2px;}
pre{background:#111c33;border:1px solid #2b3a52;border-radius:8px;padding:12px;font-size:11px;overflow-x:auto;color:#9fb3d1;}
.warn{background:#3b1d1d;border-left:4px solid #dc2626;padding:10px 14px;border-radius:8px;font-size:13px;margin:10px 0;}
img{margin:10px 0;background:#fff;padding:6px;border-radius:10px;}
a{color:#7dd3fc;}
"""


def build_report(results: dict, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    cfg = results["cfg"]
    df = results["labels_df"]
    main_label = results["main_label"]
    html = [f"<style>{CSS}</style><div class='wrap'>"]
    html.append(
        "<h1>🔬 Detector de Patrones — Sniping pump.fun</h1>"
        f"<p class='muted'>Base: <code>{os.path.basename(str(results['db_path']))}</code> · "
        f"Generado: {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')} · "
        f"{len(df):,} operaciones analizadas"
        + (" · <span class='badge'>ENTRADAS VIRTUALES SIMULADAS (no había tabla de trades propios)</span>" if results.get("virtual") else "")
        + "</p>"
    )

    # P0 (procedencia): etiqueta honesta del origen de los datos. 'synthetic' o
    # 'unknown' deben marcarse de forma prominente para que ninguna métrica de
    # este informe se confunda con evidencia de rentabilidad real.
    origin = results.get("data_origin") or "unknown"
    prov = results.get("provenance") or {}
    if origin == "synthetic":
        origin_badge = (
            "<div class='warn' style='border-color:#e74c3c;background:#3a1f1f'>"
            "⚠️ <strong>DATOS SINTÉTICOS (demo)</strong> — las métricas de este informe "
            "NO son evidencia de rentabilidad real. Úselo solo para validar el pipeline.</div>"
        )
    elif origin == "unknown":
        origin_badge = (
            "<div class='warn'>⚠️ <strong>Procedencia NO verificada</strong> — declare "
            "<code>--data-origin</code> (real) sobre una base auditada antes de tratar "
            "estas métricas como rentabilidad.</div>"
        )
    else:
        origin_badge = (
            "<div class='warn' style='border-color:#2ecc71;background:#12281c'>"
            "✅ Origen declarado: <strong>real</strong> — verifique el hash de base y el "
            "intervalo temporal antes de operar.</div>"
        )
    prov_bits = []
    if prov.get("db_sha256_16"):
        prov_bits.append(f"sha256(base)={prov['db_sha256_16']}")
    if prov.get("n_tokens") is not None:
        prov_bits.append(f"tokens={prov['n_tokens']:,}")
    if prov.get("n_operations") is not None:
        prov_bits.append(f"ops={prov['n_operations']:,}")
    if prov_bits:
        origin_badge += f"<p class='muted'>Procedencia: {' · '.join(prov_bits)}</p>"
    html.append(origin_badge)

    # ---- tarjetas resumen ----
    html.append("<div class='cards'>")
    for lc in results["label_cols"]:
        s = df[lc].dropna()
        if len(s) < 5:
            continue
        html.append(
            f"<div class='card'><div class='v'>{s.mean()*100:.1f}%</div>"
            f"<div class='l'>{LABEL_NAMES.get(lc, lc)}<br>n={len(s)}</div></div>"
        )
    m = results["models"].get(main_label, {})
    if m.get("ok"):
        html.append(f"<div class='card'><div class='v'>{m['auc']:.3f}</div><div class='l'>AUC modelo<br>(señal explotable si &gt;0.6)</div></div>")
    if results.get("pnl_total") is not None and np.isfinite(results["pnl_total"]):
        html.append(f"<div class='card'><div class='v'>{results['pnl_total']:+.1f}</div><div class='l'>PnL total (SOL)</div></div>")
    html.append("</div>")

    # ---- recomendaciones ----
    html.append("<h2>💡 Recomendaciones accionables</h2>")
    for kind, txt in build_recommendations(results):
        html.append(f"<div class='rec {kind}'>{txt}</div>")
    html.append("<p class='muted'>Reglas generadas automáticamente a partir de tus datos. Verifica siempre el tamaño de muestra (n) y el p-valor antes de cambiar tu estrategia. Esto no es consejo financiero.</p>")

    # ---- winrates ----
    html.append("<h2>📊 Winrate por definición</h2>")
    c = chart_winrates(results)
    html.append(c if c else "<p class='muted'>Sin etiquetas calculables.</p>")
    lab_rows = []
    for lc in results["label_cols"]:
        s = df[lc].dropna()
        if len(s) < 5:
            continue
        lab_rows.append({"definición": LABEL_NAMES.get(lc, lc), "n": len(s),
                          "winrate": f"{s.mean()*100:.1f}%"})
    html.append(_df_html(pd.DataFrame(lab_rows), caption="Winrate por definición de etiqueta"))

    # ---- optimización de salidas ----
    html.append(f"<h2>🎯 Optimización de salidas (grid TP×SL, ventana {cfg.mfe_window_min:.0f} min)</h2>")
    c = chart_grid(results)
    html.append(c if c else "<p class='muted'>No hay rutas de precio suficientes para simular salidas.</p>")
    c = chart_mfe_mae(results)
    if c:
        html.append("<h3>Distribución de excursiones (MFE/MAE)</h3>" + c)

    # ---- Rendimiento Institucional (QuantStats / PyFolio) ----
    qs = results.get("quantstats")
    if qs:
        html.append("<h2>📈 Rendimiento Institucional & Curva de Capital (PyFolio / QuantStats)</h2>")
        html.append("<div class='cards'>")
        html.append(f"<div class='card'><div class='v' style='color:#10b981;'>{qs.get('winrate_pct', 0.0):.1f}%</div><div class='l'>Winrate OOS Filtrado</div></div>")
        html.append(f"<div class='card'><div class='v' style='color:#38bdf8;'>{qs.get('profit_factor', 0.0):.2f}</div><div class='l'>Profit Factor Real</div></div>")
        html.append(f"<div class='card'><div class='v' style='color:#a855f7;'>{qs.get('expectancy_r', 0.0):+.2f}R</div><div class='l'>Expectancy por Trade</div></div>")
        html.append(f"<div class='card'><div class='v'>{qs.get('sortino_ratio', 0.0):.2f}</div><div class='l'>Sortino Ratio (Anualizado)</div></div>")
        html.append(f"<div class='card'><div class='v'>{qs.get('calmar_ratio', 0.0):.2f}</div><div class='l'>Calmar Ratio (CAGR/MDD)</div></div>")
        html.append(f"<div class='card'><div class='v' style='color:#f43f5e;'>{qs.get('max_drawdown_pct', 0.0):.1f}%</div><div class='l'>Max Drawdown</div></div>")
        html.append(f"<div class='card'><div class='v'>{qs.get('max_drawdown_duration_trades', 0)}</div><div class='l'>Max DD Duration (Trades)</div></div>")
        html.append(f"<div class='card'><div class='v' style='color:#fbbf24;'>{qs.get('cornish_fisher_var95_pct', 0.0):.1f}%</div><div class='l'>VaR 95% Cornish-Fisher</div></div>")
        html.append("</div>")

    c_eq = chart_equity_comparison(results)
    if c_eq:
        html.append("<h3>Evolución de Capital: Baseline sin Filtro vs Alpha Strategy vs Kelly</h3>" + c_eq)

    sim_widget = build_interactive_simulator(results)
    if sim_widget:
        html.append(sim_widget)

    # ---- Salud y Optimización de Base de Datos (DBMS Advisor) ----
    db_health = results.get("db_health")
    if db_health:
        html.append("<h2>⚡ Salud, Almacenamiento y Optimización de Base de Datos (DBMS Advisor)</h2>")
        html.append("<p class='muted'>Diagnóstico físico de almacenamiento, fragmentación, planes de consulta (EXPLAIN) y PRAGMAs óptimos para SQLite/DuckDB:</p>")
        db_metrics_df = pd.DataFrame([
            {"Métrica": "Tamaño Total", "Valor": f"{db_health.total_size_mb} MB"},
            {"Métrica": "Páginas Totales", "Valor": f"{db_health.page_count:,} (página: {db_health.page_size_bytes} B)"},
            {"Métrica": "Freelist (Espacio Libre)", "Valor": f"{db_health.freelist_count:,} págs ({db_health.freelist_mb} MB)"},
            {"Métrica": "Fragmentación / Bloat", "Valor": f"{db_health.bloat_pct:.2f}%"},
            {"Métrica": "Modo Journal Actual", "Valor": db_health.journal_mode},
            {"Métrica": "Modo Journal Recomendado", "Valor": db_health.optimal_pragmas['journal_mode']['recommended']},
            {"Métrica": "Integridad Física", "Valor": db_health.integrity_status},
        ])
        html.append(_df_html(db_metrics_df, caption="Métricas de salud de la base de datos"))
        if db_health.recommendations:
            html.append("<h3>💡 Índices Recomendados para Eliminar SCAN TABLE O(N)</h3>")
            rec_rows = []
            for r in db_health.recommendations:
                rec_rows.append({
                    "Índice": r.index_name,
                    "Tabla": r.table,
                    "Columnas": ", ".join(r.columns),
                    "Impacto Estimado": r.estimated_impact,
                    "Razón Técnica": r.rationale,
                })
            html.append(_df_html(pd.DataFrame(rec_rows), caption="Índices recomendados para eliminar SCAN TABLE"))

    # ---- Arquetipos Identificados por Machine Learning ----
    id_archs = results.get("identified_archetypes", {})
    if id_archs:
        html.append("<h2>🧬 Arquetipos Identificados de Microestructura</h2>")
        html.append("<p class='muted'>Clasificación automática de patrones y distribución poblacional:</p>")
        arch_rows = [{"Arquetipo": k, "Frecuencia": f"{v:,}"} for k, v in id_archs.items()]
        html.append(_df_html(pd.DataFrame(arch_rows), caption="Arquetipos de microestructura identificados"))

    # ---- Aprendizaje de Ganancias (Win Archetypes) ----
    archetypes = results.get("win_archetypes", [])
    if archetypes:
        html.append("<h2>🏆 Arquetipos de Ganancia Aprendidos (Winners DNA)</h2>")
        html.append("<p class='muted'>Patrones estructurales que generan asimetría positiva descubiertos en operaciones reales:</p>")
        w_rows = []
        for wa in archetypes:
            w_rows.append({
                "Arquetipo de Ganancia": wa.name,
                "Trades": f"{wa.trade_count:,}",
                "Winrate": f"{wa.winrate*100:.1f}%",
                "Retorno Medio": f"+{wa.avg_gain_pct:.1f}%",
                "Ganancia USD": f"${wa.total_gain_usd:,.1f}",
                "Acción Recomendada": wa.recommended_action
            })
        html.append(_df_html(pd.DataFrame(w_rows), caption="Arquetipos de ganancia aprendidos"))

    # ---- Descomposición de Pérdidas (Loss Learner) ----
    clusters = results.get("failure_clusters", [])
    if clusters:
        html.append("<h2>🩸 Descomposición Forense y Aprendizaje de Pérdidas</h2>")
        html.append("<p class='muted'>Causas raíz de pérdida identificadas automáticamente por el motor de machine learning post-mortem:</p>")
        c_rows = []
        for c in clusters:
            c_rows.append({
                "Cluster de Fallo": c.name,
                "Trades": f"{c.trade_count:,}",
                "% Pérdida Total": f"{c.loss_share_pct:.1f}%",
                "Pérdida Media": f"{c.avg_loss_pct:+.1f}%",
                "Veto de Protección Extraído": c.recommended_veto
            })
        html.append(_df_html(pd.DataFrame(c_rows), caption="Clusters de fallo y vetos recomendados"))

    # ---- reglas por etiqueta ----
    html.append("<h2>🧩 Patrones detectados (reglas IF-THEN)</h2>")
    for lc in results["label_cols"]:
        rules = results["rules"].get(lc)
        if rules is None or not len(rules):
            continue
        show = rules[["rule", "n", "wins", "winrate", "baseline", "lift", "p_value"]].rename(columns={
            "rule": "regla (todas las condiciones a la vez)", "n": "n_ops", "wins": "wins",
            "winrate": "winrate", "baseline": "winrate_base", "lift": "lift", "p_value": "p_valor",
        })
        html.append(f"<h3>{LABEL_NAMES.get(lc, lc)}</h3>")
        html.append(_df_html(show, 25, caption=f"Reglas IF-THEN para {LABEL_NAMES.get(lc, lc)}"))

    # ---- univariado ----
    html.append("<h2>🔎 Winrate por nivel de característica</h2>")
    c = chart_uni_top(results, main_label, cfg.top_features_charts)
    html.append(c if c else "<p class='muted'>Sin datos.</p>")
    spread = results["spread"].get(main_label)
    if spread is not None and len(spread):
        html.append("<h3>Características con mayor diferencia de winrate entre niveles</h3>")
        html.append(_df_html(spread.head(15).rename(columns={
            "feature": "feature", "desc": "descripción", "wr_min": "winrate_mín",
            "wr_max": "winrate_máx", "spread": "diferencia", "n_total": "n", "p_min": "p_mín"}),
            caption="Características con mayor diferencia de winrate entre niveles"))

    # ---- interacciones ----
    html.append("<h2>🔀 Interacciones destacadas (pares de condiciones)</h2>")
    inter = results["inter"].get(main_label)
    html.append(_df_html(inter.rename(columns={
        "combo": "combinación", "n": "n_ops", "winrate": "winrate",
        "lift": "lift", "p_value": "p_valor"}) if inter is not None else None, 15,
        caption="Interacciones destacadas (pares de condiciones)"))

    # ---- modelo ----
    html.append("<h2>🤖 Modelo predictivo (out-of-fold)</h2>")
    c1, c2 = chart_importance(results), chart_roc(results)
    html.append((c1 or "") + (c2 or "") or "<p class='muted'>Muestra insuficiente para entrenar.</p>")

    # ---- esquema y mapeo ----
    html.append("<h2>🗄️ Datos detectados y mapeo semántico</h2>")
    html.append(f"<pre>{schema_report(results['schema'])}</pre>")
    for w in results.get("warnings", []):
        html.append(f"<div class='warn'>⚠️ {w}</div>")

    # ---- metodología ----
    html.append(
        "<h2>📚 Metodología y limitaciones</h2>"
        "<ul style='font-size:13px;line-height:1.7;color:#cbd5e1;'>"
        "<li><b>Etiquetas:</b> win_real (PnL cerrado &gt; 0), win_tp_sl (toca +TP% antes que -SL% sobre la ruta real de trades), "
        "win_fut_Nm (retorno ≥ umbral N minutos después de la entrada) y win_grad (el token graduó/migró).</li>"
        "<li><b>Reglas:</b> árbol de decisión de profundidad ≤3; solo se muestran hojas con soporte mínimo y lift ≥ umbral. "
        "El p-valor es un test binomial vs winrate base (exploratorio, no corregido por múltiples comparaciones → riesgo de falsos positivos).</li>"
        "<li><b>Modelo:</b> HistGradientBoosting con AUC en 5-fold cross-validation; la importancia es por permutación.</li>"
        "<li><b>Limitaciones clave:</b> 1) <i>Overfitting</i>: con pocas operaciones los patrones no generalizan; "
        "2) <i>Slippage/MEV</i>: los precios ejecutados en sniping difieren de la ruta observada; "
        "3) <i>Survivorship</i>: si tu base solo contiene tokens que tradeaste, no ves el universo completo; "
        "4) el mercado memecoin cambia rápido: re-entrena cada pocas semanas; "
        "5) nada de esto es consejo financiero.</li></ul>"
    )
    html.append("</div>")

    path = os.path.join(out_dir, "reporte.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>Detector de Patrones — pump.fun</title></head><body>"
                + "\n".join(html) + "</body></html>")
    return path
