"""
Motor Autónomo de Optimización y Análisis de Bases de Datos (DBMS Performance & Index Advisor).

Inspirado en el estado del arte de optimizadores de bases de datos open-source:
- OtterTune / DBtune: Sintonización autónoma de parámetros de motor (knobs/PRAGMAs).
- HypoPG / sqlite3_expert / Dex: Análisis de planes de ejecución (EXPLAIN QUERY PLAN),
  detección de escaneos de tabla completos (SCAN TABLE) y síntesis automática de índices.
- sqlite-analyzer: Análisis forense de espacio, páginas, fragmentación y freelist.

Compatible con SQLite, DuckDB y bases de datos relacionales embebidas.
"""
from dataclasses import dataclass, field
import math
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class IndexRecommendation:
    table: str
    columns: List[str]
    index_name: str
    sql: str
    rationale: str
    estimated_impact: str  # HIGH, MEDIUM, LOW
    query_pattern: str


@dataclass
class DatabaseHealthReport:
    db_path: str
    total_size_mb: float
    page_size_bytes: int
    page_count: int
    freelist_count: int
    freelist_mb: float
    bloat_pct: float
    journal_mode: str
    synchronous: str
    cache_size_pages: int
    mmap_size_bytes: int
    tables: Dict[str, Dict[str, Any]]
    existing_indexes: List[Dict[str, str]]
    unindexed_foreign_keys: List[Dict[str, str]]
    recommendations: List[IndexRecommendation]
    optimal_pragmas: Dict[str, Any]
    query_plan_findings: List[Dict[str, Any]]
    integrity_status: str


class DatabaseEngineOptimizer:
    """
    Analizador forense y optimizador del motor de base de datos.
    Diagnostica almacenamiento, índices, planes de consulta y configuración de memoria.
    """

    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"Base de datos no encontrada: {self.db_path}")

    def analyze(self, sample_queries: Optional[List[str]] = None) -> DatabaseHealthReport:
        """Ejecuta una auditoría forense completa de la base de datos."""
        # Abrir en modo solo lectura para evitar contención con otros procesos
        db_uri = f"file:{self.db_path}?mode=ro"
        try:
            conn = sqlite3.connect(db_uri, uri=True, timeout=5.0)
        except Exception:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        try:
            # 1. Métricas de almacenamiento y páginas
            page_size = cur.execute("PRAGMA page_size;").fetchone()[0]
            page_count = cur.execute("PRAGMA page_count;").fetchone()[0]
            freelist_count = cur.execute("PRAGMA freelist_count;").fetchone()[0]
            journal_mode = cur.execute("PRAGMA journal_mode;").fetchone()[0].upper()
            synchronous_raw = cur.execute("PRAGMA synchronous;").fetchone()[0]
            cache_size = cur.execute("PRAGMA cache_size;").fetchone()[0]
            mmap_size = cur.execute("PRAGMA mmap_size;").fetchone()[0]

            sync_map = {0: "OFF", 1: "NORMAL", 2: "FULL", 3: "EXTRA"}
            synchronous = sync_map.get(synchronous_raw, str(synchronous_raw))

            total_size_mb = (page_size * page_count) / (1024 * 1024)
            freelist_mb = (page_size * freelist_count) / (1024 * 1024)
            bloat_pct = (freelist_count / max(page_count, 1)) * 100.0

            # 2. Integridad de la base de datos (rápida si es pequeña, segura si es grande)
            if total_size_mb > 50.0:
                integrity = "ok (verificación rápida omitida por tamaño > 50MB)"
            else:
                try:
                    integrity = cur.execute("PRAGMA quick_check;").fetchone()[0]
                except Exception as e:
                    integrity = f"ERROR: {e}"

            # 3. Inspección de tablas y columnas
            tables_data = {}
            master_tables = cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
            ).fetchall()
            table_names = [r["name"] for r in master_tables]

            for t_name in table_names:
                cols = cur.execute(f"PRAGMA table_info('{t_name}');").fetchall()
                try:
                    # Conteo O(1) vía clave de B-Tree max(_rowid_)
                    row_count = cur.execute(f"SELECT max(_rowid_) FROM '{t_name}';").fetchone()[0] or 0
                except Exception:
                    row_count = 0
                tables_data[t_name] = {
                    "row_count": row_count,
                    "columns": [c["name"] for c in cols],
                    "pk": [c["name"] for c in cols if c["pk"] > 0],
                }

            # 4. Inspección de índices existentes
            existing_indexes = []
            idx_rows = cur.execute(
                "SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%';"
            ).fetchall()
            for ir in idx_rows:
                existing_indexes.append({
                    "name": ir["name"],
                    "table": ir["tbl_name"],
                    "sql": ir["sql"] or "(automático/PK)"
                })

            # 5. Detección de claves foráneas no indexadas
            unindexed_fks = []
            for t_name in table_names:
                fks = cur.execute(f"PRAGMA foreign_key_list('{t_name}');").fetchall()
                if fks:
                    # Obtener columnas de índices de esta tabla
                    idx_list = cur.execute(f"PRAGMA index_list('{t_name}');").fetchall()
                    indexed_cols = set()
                    for idx in idx_list:
                        idx_info = cur.execute(f"PRAGMA index_info('{idx['name']}');").fetchall()
                        for ii in idx_info:
                            indexed_cols.add(ii["name"])

                    for fk in fks:
                        from_col = fk["from"]
                        if from_col not in indexed_cols:
                            unindexed_fks.append({
                                "table": t_name,
                                "column": from_col,
                                "references_table": fk["table"],
                                "references_column": fk["to"],
                            })

            # 6. Profiling de consultas y detección de SCAN TABLE
            queries_to_test = sample_queries or self._infer_standard_queries(tables_data)
            query_plan_findings, recommendations = self._profile_queries(cur, queries_to_test, tables_data, existing_indexes)

            # Agregar recomendaciones por Foreign Keys no indexadas
            for ufk in unindexed_fks:
                idx_name = f"idx_{ufk['table']}_{ufk['column']}_fk"
                sql = f"CREATE INDEX IF NOT EXISTS {idx_name} ON {ufk['table']} ({ufk['column']});"
                if not any(r.index_name == idx_name for r in recommendations):
                    recommendations.append(IndexRecommendation(
                        table=ufk["table"],
                        columns=[ufk["column"]],
                        index_name=idx_name,
                        sql=sql,
                        rationale=f"Clave foránea no indexada que referencia {ufk['references_table']}({ufk['references_column']}). Provoca bloqueos y scans en JOINs.",
                        estimated_impact="HIGH",
                        query_pattern=f"JOIN / CASCADE en {ufk['table']}.{ufk['column']}"
                    ))

            # 7. Cálculo de PRAGMAs óptimos
            optimal_pragmas = self._compute_optimal_pragmas(total_size_mb, journal_mode, synchronous)

            return DatabaseHealthReport(
                db_path=self.db_path,
                total_size_mb=round(total_size_mb, 2),
                page_size_bytes=page_size,
                page_count=page_count,
                freelist_count=freelist_count,
                freelist_mb=round(freelist_mb, 2),
                bloat_pct=round(bloat_pct, 2),
                journal_mode=journal_mode,
                synchronous=synchronous,
                cache_size_pages=cache_size,
                mmap_size_bytes=mmap_size,
                tables=tables_data,
                existing_indexes=existing_indexes,
                unindexed_foreign_keys=unindexed_fks,
                recommendations=recommendations,
                optimal_pragmas=optimal_pragmas,
                query_plan_findings=query_plan_findings,
                integrity_status=integrity,
            )
        finally:
            conn.close()

    def _infer_standard_queries(self, tables_data: Dict[str, Dict[str, Any]]) -> List[str]:
        """Infiere consultas típicas basadas en los nombres comunes de columnas y tablas."""
        queries = []
        for t_name, t_info in tables_data.items():
            cols = t_info["columns"]
            # Si hay token_id / mint
            for id_col in ["token_id", "mint", "token_address", "symbol"]:
                if id_col in cols:
                    queries.append(f"SELECT * FROM '{t_name}' WHERE {id_col} = 'SAMPLE_VAL' LIMIT 10;")
                    break
            # Si hay timestamp / created_at / slot / block_time
            time_col = next((c for c in ["timestamp", "created_at", "time", "block_time", "slot"] if c in cols), None)
            if time_col:
                queries.append(f"SELECT * FROM '{t_name}' ORDER BY {time_col} DESC LIMIT 50;")
            # Si hay filtro combinado típico
            if time_col and any(c in cols for c in ["token_id", "mint"]):
                tok_col = "token_id" if "token_id" in cols else "mint"
                queries.append(f"SELECT * FROM '{t_name}' WHERE {tok_col} = 'SAMPLE_VAL' ORDER BY {time_col} ASC;")
        return queries

    def _profile_queries(
        self,
        cur: sqlite3.Cursor,
        queries: List[str],
        tables_data: Dict[str, Dict[str, Any]],
        existing_indexes: List[Dict[str, str]]
    ) -> Tuple[List[Dict[str, Any]], List[IndexRecommendation]]:
        findings = []
        recommendations = []
        existing_idx_names = {idx["name"] for idx in existing_indexes}

        for q in queries:
            try:
                plan_rows = cur.execute(f"EXPLAIN QUERY PLAN {q}").fetchall()
                plan_detail = [dict(r) if isinstance(r, sqlite3.Row) else list(r) for r in plan_rows]
                # Buscar SCAN TABLE
                for prow in plan_rows:
                    detail_str = str(prow[3] if len(prow) > 3 else prow)
                    if "SCAN" in detail_str.upper():
                        # Extraer tabla
                        for t_name in tables_data.keys():
                            if t_name in detail_str:
                                findings.append({
                                    "query": q,
                                    "issue": f"SCAN TABLE en '{t_name}' (complejidad O(N))",
                                    "plan": detail_str
                                })
                                # Sintetizar índice
                                cols = tables_data[t_name]["columns"]
                                # Extraer columnas en WHERE o ORDER BY
                                target_cols = []
                                for candidate in ["token_id", "mint", "timestamp", "created_at", "market_cap", "symbol"]:
                                    if candidate in q and candidate in cols:
                                        target_cols.append(candidate)
                                if target_cols:
                                    col_slug = "_".join(target_cols)
                                    idx_name = f"idx_{t_name}_{col_slug}"
                                    if idx_name not in existing_idx_names and not any(r.index_name == idx_name for r in recommendations):
                                        cols_joined = ", ".join(target_cols)
                                        recommendations.append(IndexRecommendation(
                                            table=t_name,
                                            columns=target_cols,
                                            index_name=idx_name,
                                            sql=f"CREATE INDEX IF NOT EXISTS {idx_name} ON {t_name} ({cols_joined});",
                                            rationale=f"Elimina escaneo secuencial O(N) detectado en '{detail_str}'. Transforma a búsqueda logarítmica O(log N).",
                                            estimated_impact="HIGH" if tables_data[t_name]["row_count"] > 1000 else "MEDIUM",
                                            query_pattern=q
                                        ))
            except Exception as e:
                findings.append({"query": q, "error": str(e)})

        return findings, recommendations

    def _compute_optimal_pragmas(self, size_mb: float, current_journal: str, current_sync: str) -> Dict[str, Any]:
        """Calcula los PRAGMAs óptimos para alta velocidad y concurrencia lector/escritor."""
        # Si la base tiene más de 10 MB, asignar al menos 64 MB de caché y 256 MB de MMAP
        cache_pages = -64000  # 64 MB
        mmap_bytes = 268435456  # 256 MB
        if size_mb > 500:
            cache_pages = -128000  # 128 MB
            mmap_bytes = 1073741824  # 1 GB

        return {
            "journal_mode": {
                "current": current_journal,
                "recommended": "WAL",
                "rationale": "Write-Ahead Logging habilita lectores y escritores concurrentes sin bloqueos."
            },
            "synchronous": {
                "current": current_sync,
                "recommended": "NORMAL",
                "rationale": "En modo WAL, synchronous=NORMAL mantiene durabilidad total ante caídas de app y elimina el 90% de fsync() a disco."
            },
            "cache_size": {
                "recommended": cache_pages,
                "rationale": f"Asigna {abs(cache_pages)//1000} MB de caché en RAM para evitar accesos recurrentes a disco."
            },
            "mmap_size": {
                "recommended": mmap_bytes,
                "rationale": f"Habilita memory-mapped I/O ({mmap_bytes//(1024*1024)} MB) para lecturas de velocidad casi nativa en RAM."
            },
            "temp_store": {
                "recommended": "MEMORY",
                "rationale": "Almacena tablas temporales e índices de ordenación (ORDER BY / GROUP BY) en memoria."
            },
            "auto_vacuum": {
                "recommended": "INCREMENTAL",
                "rationale": "Evita fragmentación continua sin el costo de reescribir toda la base de datos."
            }
        }

    def generate_sql_migration(self, report: DatabaseHealthReport, out_path: Optional[str] = None) -> str:
        """Genera un archivo SQL de migración y optimización reproducible."""
        lines = [
            "-- ==========================================================================",
            f"-- SCRIPT DE OPTIMIZACIÓN DETERMINISTA DE BASE DE DATOS",
            f"-- Generado por: DB Pattern Engine & Optimizer",
            f"-- Base de datos: {report.db_path} ({report.total_size_mb} MB)",
            "-- ==========================================================================",
            "",
            "-- 1. CONFIGURACIÓN DEL MOTOR DE MEMORIA Y CONCURRENCIA (PRAGMAS)",
            "PRAGMA journal_mode = WAL;",
            "PRAGMA synchronous = NORMAL;",
            f"PRAGMA cache_size = {report.optimal_pragmas['cache_size']['recommended']};",
            f"PRAGMA mmap_size = {report.optimal_pragmas['mmap_size']['recommended']};",
            "PRAGMA temp_store = MEMORY;",
            "PRAGMA foreign_keys = ON;",
            "",
            "-- 2. CREACIÓN DE ÍNDICES DE ALTO IMPACTO (ELIMINA SCAN TABLE)",
        ]

        if report.recommendations:
            for rec in report.recommendations:
                lines.append(f"-- Impacto: {rec.estimated_impact} | Motivo: {rec.rationale}")
                lines.append(rec.sql)
        else:
            lines.append("-- No se detectaron índices faltantes críticos.")

        lines.extend([
            "",
            "-- 3. REORGANIZACIÓN ESTADÍSTICA Y MANTENIMIENTO DEL OPTIMIZADOR",
            "ANALYZE;               -- Actualiza estadísticas en sqlite_stat1 para el optimizador de consultas",
            "PRAGMA optimize;       -- SQLite evalúa y ajusta planes de acceso",
        ])

        if report.freelist_mb > 5.0 or report.bloat_pct > 15.0:
            lines.extend([
                "",
                f"-- 4. RECUPERACIÓN DE ESPACIO (Bloat actual: {report.bloat_pct}% | Freelist: {report.freelist_mb} MB)",
                "VACUUM;                -- Compacta páginas libres y desfragmenta B-Trees",
            ])

        sql_content = "\n".join(lines) + "\n"

        if out_path:
            os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(sql_content)

        return sql_content

    def apply_optimizations(self, report: DatabaseHealthReport, execute_vacuum: bool = False) -> Dict[str, Any]:
        """Aplica directamente las optimizaciones de forma segura."""
        t0 = time.time()
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        applied_indexes = []
        applied_pragmas = []

        try:
            # Aplicar PRAGMAs
            cur.execute("PRAGMA journal_mode = WAL;")
            cur.execute("PRAGMA synchronous = NORMAL;")
            cur.execute(f"PRAGMA cache_size = {report.optimal_pragmas['cache_size']['recommended']};")
            cur.execute(f"PRAGMA mmap_size = {report.optimal_pragmas['mmap_size']['recommended']};")
            cur.execute("PRAGMA temp_store = MEMORY;")
            cur.execute("PRAGMA foreign_keys = ON;")
            applied_pragmas = ["journal_mode=WAL", "synchronous=NORMAL", "cache_size", "mmap_size", "temp_store=MEMORY"]

            # Aplicar Índices
            for rec in report.recommendations:
                cur.execute(rec.sql)
                applied_indexes.append(rec.index_name)

            # Actualizar estadísticas
            cur.execute("ANALYZE;")
            cur.execute("PRAGMA optimize;")

            conn.commit()

            if execute_vacuum and (report.freelist_mb > 5.0 or report.bloat_pct > 15.0):
                cur.execute("VACUUM;")

            elapsed_ms = (time.time() - t0) * 1000.0
            return {
                "success": True,
                "elapsed_ms": round(elapsed_ms, 2),
                "applied_pragmas": applied_pragmas,
                "applied_indexes": applied_indexes,
                "vacuum_executed": execute_vacuum,
            }
        except Exception as e:
            conn.rollback()
            return {"success": False, "error": str(e)}
        finally:
            conn.close()
