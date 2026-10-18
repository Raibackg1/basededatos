#!/usr/bin/env python3
"""
Dashboard FastAPI para Revisión Humana-en-el-Bucle (Human-in-the-Loop).
======================================================================

Endpoints REST para revisión filter-by-filter de señales sobre AnnotationDB:
- GET  /pending          - Señales pendientes de revisión
- POST /review           - Enviar anotación completa de una señal
- GET  /filter-effectiveness - Estadísticas de efectividad por filtro
- GET  /reviewed         - Señales ya revisadas
- POST /session          - Crear sesión de revisión
- GET  /health           - Health check

Modo: solo lectura sobre custom.db, lectura/escritura sobre AnnotationDB.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from pumpfun_detector.detector.manual_annotation import (
    AnnotationDB,
    FilterAnnotation,
    FilterVerdict,
    SignalReview,
    SignalVerdict,
)

app = FastAPI(
    title="PumpScope HITL Dashboard",
    description="Dashboard de revisión humana para señales de trading Pump.fun",
    version="1.0.0",
)

# Rutas canónicas via variables de entorno
ANNOTATION_DB = os.environ.get("PUMPSCOPE_ANNOTATION_DB", "annotation_reviews.db")
CUSTOM_DB = os.environ.get("PUMPSCOPE_DB", "custom.db")


def get_annotation_db() -> AnnotationDB:
    """Obtiene instancia de AnnotationDB (singleton por request)."""
    return AnnotationDB(ANNOTATION_DB)


# ── Modelos de request/response ──────────────────────────────────────────

class FilterAnnotationRequest(BaseModel):
    filter_name: str
    filter_value: Any
    threshold: Any
    verdict: str  # "pass", "fail", "false_positive", "unsure"
    notes: Optional[str] = ""
    reviewer: str


class SignalReviewRequest(BaseModel):
    signal_id: str
    token_id: str
    branch: str
    admitted: bool
    features_json: str
    metrics_json: str
    global_verdict: str  # "buy", "veto", "review_needed", "false_alarm"
    filter_annotations: List[FilterAnnotationRequest]
    reviewer: str
    session_id: str


class SessionRequest(BaseModel):
    reviewer: str
    notes: Optional[str] = ""


class SessionResponse(BaseModel):
    session_id: str
    reviewer: str
    started_at_ms: int


# ── Endpoints ────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """Health check del dashboard."""
    return {
        "status": "ok",
        "annotation_db": ANNOTATION_DB,
        "custom_db": CUSTOM_DB,
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
    }


@app.get("/pending")
def pending_signals(limit: int = Query(50, ge=1, le=200)):
    """Obtiene señales pendientes de revisión humana."""
    db = get_annotation_db()
    try:
        signals = db.get_pending_signals(limit=limit)
        return {
            "count": len(signals),
            "signals": signals,
            "annotation_db": ANNOTATION_DB,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/review")
def submit_review(review: SignalReviewRequest):
    """Guarda una revisión completa de señal con anotaciones por filtro."""
    try:
        # Convertir veredictos de string a Enum
        verdict_map = {
            "buy": SignalVerdict.BUY,
            "veto": SignalVerdict.VETO,
            "review_needed": SignalVerdict.REVIEW_NEEDED,
            "false_alarm": SignalVerdict.FALSE_ALARM,
        }
        filter_verdict_map = {
            "pass": FilterVerdict.PASS,
            "fail": FilterVerdict.FAIL,
            "false_positive": FilterVerdict.FALSE_POSITIVE,
            "unsure": FilterVerdict.UNSURE,
        }

        global_verdict = verdict_map.get(review.global_verdict)
        if global_verdict is None:
            raise HTTPException(status_code=400, detail=f"global_verdict inválido: {review.global_verdict}")

        filter_annotations = []
        for fa in review.filter_annotations:
            fv = filter_verdict_map.get(fa.verdict)
            if fv is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"verdict inválido para filtro {fa.filter_name}: {fa.verdict}",
                )
            filter_annotations.append(
                FilterAnnotation(
                    filter_name=fa.filter_name,
                    filter_value=fa.filter_value,
                    threshold=fa.threshold,
                    verdict=fv,
                    notes=fa.notes or "",
                    reviewer=fa.reviewer,
                    timestamp_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
                )
            )

        signal_review = SignalReview(
            signal_id=review.signal_id,
            token_id=review.token_id,
            branch=review.branch,
            admitted=review.admitted,
            features_json=review.features_json,
            metrics_json=review.metrics_json,
            global_verdict=global_verdict,
            filter_annotations=filter_annotations,
            reviewer=review.reviewer,
            created_at_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
            updated_at_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
            session_id=review.session_id,
        )

        db = get_annotation_db()
        db.save_review(signal_review)

        return {
            "status": "saved",
            "signal_id": signal_review.signal_id,
            "session_id": signal_review.session_id,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/filter-effectiveness")
def filter_effectiveness():
    """Obtiene estadísticas de efectividad de todos los filtros."""
    db = get_annotation_db()
    try:
        stats = db.get_filter_effectiveness()
        return {
            "annotation_db": ANNOTATION_DB,
            "total_filters": len(stats),
            "filters": stats,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/reviewed")
def reviewed_signals(session_id: Optional[str] = Query(None)):
    """Obtiene señales ya revisadas, opcionalmente filtradas por sesión."""
    db = get_annotation_db()
    try:
        signals = db.get_reviewed_signals(session_id=session_id)
        return {
            "count": len(signals),
            "session_id": session_id,
            "signals": signals,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/session", response_model=SessionResponse)
def create_session(req: SessionRequest):
    """Crea una nueva sesión de revisión humana."""
    db = get_annotation_db()
    try:
        session_id = db.create_session(reviewer=req.reviewer, notes=req.notes or "")
        # Obtener el started_at_ms de la sesión creada
        with sqlite3_connect(ANNOTATION_DB) as con:
            row = con.execute(
                "SELECT started_at_ms FROM review_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        started_at = row[0] if row else 0
        return SessionResponse(
            session_id=session_id,
            reviewer=req.reviewer,
            started_at_ms=started_at,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/session/{session_id}/end")
def end_session(session_id: str):
    """Termina una sesión de revisión."""
    db = get_annotation_db()
    try:
        db.end_session(session_id)
        return {"status": "ended", "session_id": session_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/stats")
def dashboard_stats():
    """Estadísticas agregadas del dashboard para el resumen ejecutivo."""
    db = get_annotation_db()
    try:
        filters = db.get_filter_effectiveness()
        reviewed = db.get_reviewed_signals()

        total_reviews = len(reviewed)
        buy_count = sum(1 for s in reviewed if s["global_verdict"] == "buy")
        veto_count = sum(1 for s in reviewed if s["global_verdict"] == "veto")
        review_needed = sum(1 for s in reviewed if s["global_verdict"] == "review_needed")
        false_alarm = sum(1 for s in reviewed if s["global_verdict"] == "false_alarm")

        # Filtros más problemáticos (más FP o FN)
        problem_filters = sorted(
            filters,
            key=lambda f: f["false_positives"] + f["false_negatives"],
            reverse=True,
        )[:5]

        return {
            "annotation_db": ANNOTATION_DB,
            "total_reviews": total_reviews,
            "verdicts": {
                "buy": buy_count,
                "veto": veto_count,
                "review_needed": review_needed,
                "false_alarm": false_alarm,
            },
            "total_filters_tracked": len(filters),
            "reliable_filters": sum(1 for f in filters if f["total_cases"] >= 5),
            "problem_filters": problem_filters,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Utilidades ───────────────────────────────────────────────────────────

import sqlite3


def sqlite3_connect(db_path: str):
    return sqlite3.connect(db_path)


# ── Página HTML simple para revisión rápida ──────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard_home():
    """Página principal del dashboard con interfaz básica de revisión."""
    html = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>PumpScope HITL Dashboard</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    margin: 0; padding: 20px; background: #0f172a; color: #f8fafc; }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            h1 {{ color: #38bdf8; margin-bottom: 24px; }}
            .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                          gap: 16px; margin-bottom: 32px; }}
            .stat-card {{ background: #1e293b; padding: 20px; border-radius: 8px;
                          border: 1px solid #334155; }}
            .stat-value {{ font-size: 28px; font-weight: bold; color: #38bdf8; }}
            .stat-label {{ font-size: 14px; color: #94a3b8; margin-top: 4px; }}
            .filters-table {{ background: #1e293b; border-radius: 8px;
                             border: 1px solid #334155; overflow: hidden; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 12px 16px; text-align: left; border-bottom: 1px solid #334155; }}
            th {{ background: #0f172a; color: #94a3b8; font-size: 12px; text-transform: uppercase; }}
            tr:hover {{ background: #334155; }}
            .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px;
                      font-size: 11px; font-weight: bold; }}
            .badge-ok {{ background: #10b981; color: white; }}
            .badge-warn {{ background: #f59e0b; color: white; }}
            .badge-bad {{ background: #ef4444; color: white; }}
            .btn {{ background: #38bdf8; color: #0f172a; border: none; padding: 8px 16px;
                    border-radius: 4px; cursor: pointer; font-weight: 600; }}
            .btn:hover {{ background: #7dd3fc; }}
            .api-link {{ color: #38bdf8; text-decoration: none; }}
            .api-link:hover {{ text-decoration: underline; }}
            code {{ background: #334155; padding: 2px 6px; border-radius: 4px; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>PumpScope HITL Dashboard</h1>
            <p>Revisión humana-en-el-bucle para señales de trading Pump.fun</p>

            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-value" id="total-reviews">-</div>
                    <div class="stat-label">Total Revisadas</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" id="buy-count">-</div>
                    <div class="stat-label">Veredicto: BUY</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" id="veto-count">-</div>
                    <div class="stat-label">Veredicto: VETO</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" id="filters-tracked">-</div>
                    <div class="stat-label">Filtros Trackeados</div>
                </div>
            </div>

            <h2>Efectividad de Filtros</h2>
            <div class="filters-table">
                <table>
                    <thead>
                        <tr>
                            <th>Filtro</th><th>Casos</th><th>Precisión</th><th>Recall</th><th>F1</th><th>FP</th><th>FN</th><th>Estado</th>
                        </tr>
                    </thead>
                    <tbody id="filters-body">
                    </tbody>
                </table>
            </div>

            <h2>API Endpoints</h2>
            <ul>
                <li><a class="api-link" href="/docs" target="_blank">Swagger UI /docs</a></li>
                <li><a class="api-link" href="/pending" target="_blank">GET /pending</a> - Señales pendientes</li>
                <li><a class="api-link" href="/filter-effectiveness" target="_blank">GET /filter-effectiveness</a></li>
                <li><a class="api-link" href="/stats" target="_blank">GET /stats</a></li>
                <li><a class="api-link" href="/health" target="_blank">GET /health</a></li>
            </ul>

            <script>
                async function loadStats() {{
                    const res = await fetch('/stats');
                    const data = await res.json();
                    document.getElementById('total-reviews').textContent = data.total_reviews;
                    document.getElementById('buy-count').textContent = data.verdicts.buy;
                    document.getElementById('veto-count').textContent = data.verdicts.veto;
                    document.getElementById('filters-tracked').textContent = data.total_filters_tracked;
                }}

                async function loadFilters() {{
                    const res = await fetch('/filter-effectiveness');
                    const data = await res.json();
                    const body = document.getElementById('filters-body');
                    body.innerHTML = '';
                    data.filters.forEach(f => {{
                        const reliable = f.reliable ? 'OK' : 'INSUFICIENTE';
                        const badgeClass = reliable === 'OK' ? 'badge-ok' : 'badge-warn';
                        const row = `<tr>
                            <td><code>${{f.filter_name}}</code></td>
                            <td>${{f.total_cases}}</td>
                            <td>${{(f.precision*100).toFixed(1)}}%</td>
                            <td>${{(f.recall*100).toFixed(1)}}%</td>
                            <td>${{(f.f1_score*100).toFixed(1)}}%</td>
                            <td>${{f.false_positives}}</td>
                            <td>${{f.false_negatives}}</td>
                            <td><span class="badge ${{badgeClass}}">${{reliable}}</span></td>
                        </tr>`;
                        body.innerHTML += row;
                    }};
                }}

                loadStats();
                loadFilters();
            </script>
        </div>
    </body>
    </html>
    """

    return HTMLResponse(content=html)
