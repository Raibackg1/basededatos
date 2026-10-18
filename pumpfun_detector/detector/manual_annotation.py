#!/usr/bin/env python3
"""
Sistema de Filtrado Humano-en-el-Bucle (Human-in-the-Loop).
Permite revisión manual filter-by-filter de señales con anotaciones.
"""
from __future__ import annotations
import sqlite3, json, hashlib, os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Literal
from enum import Enum


class FilterVerdict(Enum):
    """Veredicto del revisor humano por filtro."""
    PASS = "pass"           # El filtro es correcto, deja pasar
    FAIL = "fail"           # El filtro debería vetar, pero no lo hace
    FALSE_POSITIVE = "false_positive"  # El filtro veta incorrectamente
    UNSURE = "unsure"       # No se puede determinar


class SignalVerdict(Enum):
    """Veredicto global sobre una señal."""
    BUY = "buy"                    # Comprar - señal válida
    VETO = "veto"                  # Vetar - señal inválida
    REVIEW_NEEDED = "review_needed"  # Requiere más análisis
    FALSE_ALARM = "false_alarm"    # Falsa alarma del sistema


@dataclass
class FilterAnnotation:
    """Anotación de un revisor sobre un filtro específico."""
    filter_name: str
    filter_value: Any
    threshold: Any
    verdict: FilterVerdict
    notes: str
    reviewer: str
    timestamp_ms: int


@dataclass
class SignalReview:
    """Revisión completa de una señal por un humano."""
    signal_id: str
    token_id: str
    branch: str
    admitted: bool
    features_json: str
    metrics_json: str
    
    # Veredicto global
    global_verdict: SignalVerdict
    
    # Anotaciones por filtro
    filter_annotations: List[FilterAnnotation]
    
    # Metadatos
    reviewer: str
    created_at_ms: int
    updated_at_ms: int
    session_id: str


class AnnotationDB:
    """Base de datos para anotaciones de revisión manual."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_schema()
    
    def _init_schema(self):
        schema = """
        CREATE TABLE IF NOT EXISTS signal_reviews (
            signal_id TEXT PRIMARY KEY,
            token_id TEXT NOT NULL,
            branch TEXT NOT NULL,
            admitted INTEGER NOT NULL,
            features_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            global_verdict TEXT NOT NULL,
            filter_annotations_json TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            session_id TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_reviews_token ON signal_reviews(token_id);
        CREATE INDEX IF NOT EXISTS idx_reviews_verdict ON signal_reviews(global_verdict);
        CREATE INDEX IF NOT EXISTS idx_reviews_session ON signal_reviews(session_id);
        
        CREATE TABLE IF NOT EXISTS review_sessions (
            session_id TEXT PRIMARY KEY,
            reviewer TEXT NOT NULL,
            started_at_ms INTEGER NOT NULL,
            ended_at_ms INTEGER,
            signals_reviewed INTEGER DEFAULT 0,
            notes TEXT
        );
        
        CREATE TABLE IF NOT EXISTS filter_effectiveness (
            filter_name TEXT PRIMARY KEY,
            total_cases INTEGER DEFAULT 0,
            correct_passes INTEGER DEFAULT 0,
            correct_vetoes INTEGER DEFAULT 0,
            false_positives INTEGER DEFAULT 0,
            false_negatives INTEGER DEFAULT 0,
            last_updated_ms INTEGER
        );
        CREATE TABLE IF NOT EXISTS token_annotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mint TEXT NOT NULL,
            symbol TEXT NOT NULL DEFAULT '',
            decision TEXT NOT NULL,
            rationale TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            features_json TEXT NOT NULL DEFAULT '{}',
            created_at_ms INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_token_annotations_mint ON token_annotations(mint);
        """
        con = sqlite3.connect(self.db_path)
        try:
            con.executescript(schema)
            con.commit()
        finally:
            con.close()

    def add_annotation(
        self,
        mint: str,
        symbol: str,
        decision: str,
        rationale: str = "",
        tags: Optional[List[str]] = None,
        features_snapshot: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Registra una etiqueta humana/IA (BUY / AVOID / WATCH) desde el Cockpit o API."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        dec_norm = str(decision or "WATCH").upper().strip()
        con = sqlite3.connect(self.db_path)
        try:
            cur = con.execute(
                """
                INSERT INTO token_annotations
                (mint, symbol, decision, rationale, tags_json, features_json, created_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(mint),
                    str(symbol or ""),
                    dec_norm,
                    str(rationale or ""),
                    json.dumps(list(tags or []), ensure_ascii=False),
                    json.dumps(dict(features_snapshot or {}), ensure_ascii=False),
                    now_ms,
                ),
            )
            con.commit()
            return int(cur.lastrowid or 0)
        finally:
            con.close()

    def list_annotations(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Lista las anotaciones más recientes."""
        con = sqlite3.connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT id, mint, symbol, decision, rationale, tags_json, features_json, created_at_ms
                FROM token_annotations
                ORDER BY created_at_ms DESC, id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        finally:
            con.close()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append({
                "id": r[0],
                "mint": r[1],
                "symbol": r[2],
                "decision": r[3],
                "rationale": r[4],
                "tags": json.loads(r[5]) if r[5] else [],
                "features_snapshot": json.loads(r[6]) if r[6] else {},
                "created_at_ms": r[7],
            })
        return out

    def get_summary(self) -> Dict[str, int]:
        """Resumen de etiquetas registradas para supervisión y reentrenamiento."""
        con = sqlite3.connect(self.db_path)
        try:
            rows = con.execute(
                "SELECT decision, COUNT(*) FROM token_annotations GROUP BY decision"
            ).fetchall()
        finally:
            con.close()
        counts = {str(k).upper(): int(v) for k, v in rows}
        buy_cnt = counts.get("BUY", 0)
        avoid_cnt = counts.get("AVOID", 0) + counts.get("VETO", 0)
        watch_cnt = counts.get("WATCH", 0)
        total = sum(counts.values())
        return {
            "total": total,
            "buy_count": buy_cnt,
            "avoid_count": avoid_cnt,
            "watch_count": watch_cnt,
        }

    def export_training_dataset(self) -> List[Dict[str, Any]]:
        """Exporta las anotaciones etiquetadas (BUY=1, AVOID/VETO=0) como dataset supervisado."""
        items = self.list_annotations(limit=5000)
        dataset: List[Dict[str, Any]] = []
        for it in items:
            dec = it["decision"].upper()
            if dec not in ("BUY", "AVOID", "VETO"):
                continue
            dataset.append({
                "mint": it["mint"],
                "symbol": it["symbol"],
                "label": 1 if dec == "BUY" else 0,
                "decision": dec,
                "rationale": it["rationale"],
                "tags": it["tags"],
                "features": it["features_snapshot"],
                "created_at_ms": it["created_at_ms"],
            })
        return dataset
    
    def create_session(self, reviewer: str, notes: str = "") -> str:
        """Crea una nueva sesión de revisión."""
        session_id = hashlib.sha256(f"{reviewer}{datetime.now().isoformat()}".encode()).hexdigest()[:16]
        con = sqlite3.connect(self.db_path)
        try:
            con.execute("""
                INSERT INTO review_sessions
                (session_id, reviewer, started_at_ms, notes)
                VALUES (?, ?, ?, ?)
            """, (session_id, reviewer, int(datetime.now(timezone.utc).timestamp()*1000), notes))
            con.commit()
        finally:
            con.close()
        return session_id
    
    def end_session(self, session_id: str):
        """Termina una sesión de revisión."""
        con = sqlite3.connect(self.db_path)
        try:
            con.execute("""
                UPDATE review_sessions SET ended_at_ms = ?
                WHERE session_id = ?
            """, (int(datetime.now(timezone.utc).timestamp()*1000), session_id))
            con.commit()
        finally:
            con.close()
    
    def save_review(self, review: SignalReview):
        """Guarda una revisión completa."""
        con = sqlite3.connect(self.db_path)
        try:
            con.execute("""
                INSERT OR REPLACE INTO signal_reviews
                (signal_id, token_id, branch, admitted, features_json, metrics_json,
                 global_verdict, filter_annotations_json, reviewer, created_at_ms,
                 updated_at_ms, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                review.signal_id, review.token_id, review.branch, int(review.admitted),
                review.features_json, review.metrics_json,
                review.global_verdict.value,
                json.dumps([asdict(a) for a in review.filter_annotations]),
                review.reviewer, review.created_at_ms, review.updated_at_ms, review.session_id
            ))
            con.commit()
        finally:
            con.close()
        # Actualizar estadísticas de efectividad por filtro
        self._update_filter_stats(review.filter_annotations)
    
    def _update_filter_stats(self, annotations: List[FilterAnnotation]):
        """Actualiza estadísticas de efectividad por filtro."""
        con = sqlite3.connect(self.db_path)
        try:
            for ann in annotations:
                stats = con.execute(
                    "SELECT * FROM filter_effectiveness WHERE filter_name = ?",
                    (ann.filter_name,)
                ).fetchone()
                
                if stats:
                    total, cp, cv, fp, fn, _ = stats[1:]
                    total += 1
                    if ann.verdict == FilterVerdict.PASS:
                        cp += 1
                    elif ann.verdict == FilterVerdict.FAIL:
                        fn += 1  # Debería haber vetado
                    elif ann.verdict == FilterVerdict.FALSE_POSITIVE:
                        fp += 1  # Vetó incorrectamente
                    
                    con.execute("""
                        UPDATE filter_effectiveness
                        SET total_cases = ?, correct_passes = ?, correct_vetoes = ?,
                            false_positives = ?, false_negatives = ?, last_updated_ms = ?
                        WHERE filter_name = ?
                    """, (total, cp, cv, fp, fn, int(datetime.now(timezone.utc).timestamp()*1000), ann.filter_name))
                else:
                    cp = 1 if ann.verdict == FilterVerdict.PASS else 0
                    fn = 1 if ann.verdict == FilterVerdict.FAIL else 0
                    fp = 1 if ann.verdict == FilterVerdict.FALSE_POSITIVE else 0
                    cv = 0
                    con.execute("""
                        INSERT INTO filter_effectiveness
                        (filter_name, total_cases, correct_passes, correct_vetoes,
                         false_positives, false_negatives, last_updated_ms)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (ann.filter_name, 1, cp, cv, fp, fn, int(datetime.now(timezone.utc).timestamp()*1000)))
            con.commit()
        finally:
            con.close()
    
    def get_filter_effectiveness(self) -> List[Dict[str, Any]]:
        """Obtiene efectividad de todos los filtros."""
        con = sqlite3.connect(self.db_path)
        try:
            rows = con.execute("SELECT * FROM filter_effectiveness ORDER BY total_cases DESC").fetchall()
        finally:
            con.close()
        
        result = []
        for row in rows:
            name, total, cp, cv, fp, fn, _ = row
            precision = cp / (cp + fp) if (cp + fp) > 0 else 0
            recall = cp / (cp + fn) if (cp + fn) > 0 else 0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
            
            result.append({
                "filter_name": name,
                "total_cases": total,
                "correct_passes": cp,
                "correct_vetoes": cv,
                "false_positives": fp,
                "false_negatives": fn,
                "precision": round(precision, 3),
                "recall": round(recall, 3),
                "f1_score": round(f1, 3)
            })
        return result
    
    def get_pending_signals(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Obtiene señales pendientes de revisión (no revisadas aún)."""
        con = sqlite3.connect(self.db_path)
        try:
            rows = con.execute("""
                SELECT d.token_id, d.branch, d.admitted, d.features_json, d.metrics_json,
                       d.decision_signature
                FROM decision_ledger d
                LEFT JOIN signal_reviews r ON r.signal_id = d.decision_signature
                WHERE r.signal_id IS NULL AND d.outcome = 'PENDING'
                ORDER BY d.entry_ts DESC LIMIT ?
            """, (limit,)).fetchall()
        finally:
            con.close()
        
        return [
            {
                "signal_id": row[5],
                "token_id": row[0],
                "branch": row[1],
                "admitted": bool(row[2]),
                "features": json.loads(row[3]) if row[3] else {},
                "metrics": json.loads(row[4]) if row[4] else {}
            }
            for row in rows
        ]
    
    def get_reviewed_signals(self, session_id: str = None) -> List[Dict[str, Any]]:
        """Obtiene señales ya revisadas."""
        con = sqlite3.connect(self.db_path)
        try:
            if session_id:
                rows = con.execute("""
                    SELECT * FROM signal_reviews WHERE session_id = ?
                """, (session_id,)).fetchall()
            else:
                rows = con.execute("SELECT * FROM signal_reviews ORDER BY created_at_ms DESC").fetchall()
        finally:
            con.close()
        
        return [
            {
                "signal_id": row[0],
                "token_id": row[1],
                "branch": row[2],
                "admitted": bool(row[3]),
                "global_verdict": row[6],
                "filter_annotations": json.loads(row[7]),
                "reviewer": row[8],
                "created_at": datetime.fromtimestamp(row[9]/1000, tz=timezone.utc).isoformat(),
                "session_id": row[11]
            }
            for row in rows
        ]


class ReviewInterface:
    """Interfaz CLI interactiva para revisión manual."""
    
    def __init__(self, db_path: str, reviewer: str):
        self.db = AnnotationDB(db_path)
        self.reviewer = reviewer
        self.session_id = self.db.create_session(reviewer)
        print(f"[ReviewInterface] Sesión iniciada: {self.session_id}")
    
    def review_signal(self, signal: Dict[str, Any]) -> SignalReview:
        """Interfaz interactiva para revisar una señal."""
        print(f"\n{'='*60}")
        print(f"REVISIÓN: {signal['token_id'][:12]}... | Branch: {signal['branch']} | Admitido: {signal['admitted']}")
        print(f"{'='*60}")
        
        # Mostrar features clave
        features = signal.get('features', {})
        print(f"\nFeatures clave:")
        key_features = ['firstMarketCap', 'firstLiquidity', 'market_cap', 'devBalancePct', 
                       'migrationProgress', 'realSolReserves', 'buysM5', 'sellsM5', 'holderCount']
        for k in key_features:
            if k in features:
                print(f"  {k}: {features[k]}")
        
        # Mostrar métricas
        metrics = signal.get('metrics', {})
        if metrics:
            print(f"\nMétricas:")
            for k, v in metrics.items():
                print(f"  {k}: {v}")
        
        # Veredicto global
        print(f"\nVeredicto global:")
        print("  1. BUY - Señal válida, comprar")
        print("  2. VETO - Señal inválida, vetar")
        print("  3. REVIEW_NEEDED - Requiere más análisis")
        print("  4. FALSE_ALARM - Falsa alarma del sistema")
        
        verdict_map = {
            "1": SignalVerdict.BUY,
            "2": SignalVerdict.VETO,
            "3": SignalVerdict.REVIEW_NEEDED,
            "4": SignalVerdict.FALSE_ALARM
        }
        
        while True:
            choice = input("Selecciona (1-4): ").strip()
            if choice in verdict_map:
                global_verdict = verdict_map[choice]
                break
            print("Opción inválida")
        
        # Anotaciones por filtro
        filter_annotations = []
        
        # Filtros estándar a revisar
        standard_filters = [
            ("DEAD_LAUNCH", "mc < 4500", features.get("market_cap", features.get("mc_at_entry_est", 0)), 4500),
            ("DEV_DUMP", "devBalancePct > 1.5%", features.get("devBalancePct", 0), 1.5),
            ("SATURATION", "migrationProgress > 45%", features.get("migrationProgress", 0), 45),
            ("LOW_RESERVES", "realSolReserves < 12 SOL", features.get("realSolReserves", 0), 12),
            ("SELL_PRESSURE", "sellsM5 > 50", features.get("sellsM5", 0), 50),
            ("CABALE", "topHolderPct > 30%", features.get("topHolderPct", 0), 30),
            ("BLOCK0_TRAP", "is_block0_trap", features.get("is_block0_trap", 0), 1),
            ("MICROSTRUCTURE", "buysM5 < 2", features.get("buysM5", 0), 2),
        ]
        
        print(f"\n--- Revisión por filtro ---")
        for fname, desc, fval, threshold in standard_filters:
            print(f"\nFiltro: {fname} ({desc})")
            print(f"  Valor actual: {fval} | Umbral: {threshold}")
            print("  Veredicto: 1=PASS (correcto), 2=FAIL (debería vetar), 3=FALSE_POSITIVE (vetó mal), 4=UNSURE")
            
            while True:
                fchoice = input("  Selecciona (1-4): ").strip()
                if fchoice in ["1", "2", "3", "4"]:
                    verdict_map_f = {
                        "1": FilterVerdict.PASS,
                        "2": FilterVerdict.FAIL,
                        "3": FilterVerdict.FALSE_POSITIVE,
                        "4": FilterVerdict.UNSURE
                    }
                    verdict = verdict_map_f[fchoice]
                    break
            
            notes = input("  Notas (opcional): ").strip()
            
            filter_annotations.append(FilterAnnotation(
                filter_name=fname,
                filter_value=fval,
                threshold=threshold,
                verdict=verdict,
                notes=notes,
                reviewer=self.reviewer,
                timestamp_ms=int(datetime.now(timezone.utc).timestamp()*1000)
            ))
        
        review = SignalReview(
            signal_id=signal['signal_id'],
            token_id=signal['token_id'],
            branch=signal['branch'],
            admitted=signal['admitted'],
            features_json=json.dumps(signal.get('features', {})),
            metrics_json=json.dumps(signal.get('metrics', {})),
            global_verdict=global_verdict,
            filter_annotations=filter_annotations,
            reviewer=self.reviewer,
            created_at_ms=int(datetime.now(timezone.utc).timestamp()*1000),
            updated_at_ms=int(datetime.now(timezone.utc).timestamp()*1000),
            session_id=self.session_id
        )
        
        self.db.save_review(review)
        print(f"\n[ReviewInterface] Revisión guardada: {review.signal_id}")
        
        return review
    
    def run_review_session(self, max_signals: int = 20):
        """Ejecuta sesión interactiva de revisión."""
        pending = self.db.get_pending_signals(max_signals)
        
        if not pending:
            print("[ReviewInterface] No hay señales pendientes de revisión")
            return
        
        print(f"[ReviewInterface] {len(pending)} señales pendientes")
        
        for i, signal in enumerate(pending, 1):
            print(f"\n[{i}/{len(pending)}]")
            try:
                self.review_signal(signal)
            except KeyboardInterrupt:
                print("\n[ReviewInterface] Interrumpido por usuario")
                break
            except Exception as e:
                print(f"[ReviewInterface] Error: {e}")
        
        self.db.end_session(self.session_id)
        print(f"\n[ReviewInterface] Sesión completada. Revisadas {i} señales.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Sistema de revisión manual de señales")
    parser.add_argument("--db", default=os.environ.get("PUMPSCOPE_DB", "custom.db"), help="Path a la DB")
    parser.add_argument("--reviewer", required=True, help="Nombre del revisor")
    parser.add_argument("--max", type=int, default=20, help="Máximo señales a revisar")
    parser.add_argument("--stats", action="store_true", help="Mostrar efectividad de filtros")
    args = parser.parse_args()
    
    db = AnnotationDB(args.db)
    
    if args.stats:
        print("\n=== EFECTIVIDAD DE FILTROS ===")
        for f in db.get_filter_effectiveness():
            print(f"  {f['filter_name']}: total={f['total_cases']} P={f['precision']} R={f['recall']} F1={f['f1_score']} (FP={f['false_positives']} FN={f['false_negatives']})")
        return
    
    interface = ReviewInterface(args.db, args.reviewer)
    interface.run_review_session(args.max)


if __name__ == "__main__":
    main()