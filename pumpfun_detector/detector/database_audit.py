"""Auditoría de bases SQLite en modo de solo lectura.

Este módulo deliberadamente depende solo de la biblioteca estándar y de
heurísticas puras del inspector existente.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__ as program_version
from .inspector import TABLE_PROFILES, _match_score, _norm


REPORT_SCHEMA_VERSION = 1
VALID_ORIGINS = {"unknown", "synthetic", "real"}
MAX_SAMPLE_ROWS = 10_000


def quote_identifier(identifier: str) -> str:
    """Cita un identificador SQLite sin permitir salir del contexto de nombre."""
    if not isinstance(identifier, str) or not identifier or "\x00" in identifier:
        raise ValueError("El identificador SQLite debe ser un nombre no vacío y sin NUL.")
    return '"' + identifier.replace('"', '""') + '"'


def open_readonly_db(db_path: str | Path) -> sqlite3.Connection:
    """Abre una base existente en solo lectura y fija una vista de lectura."""
    path = Path(db_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise ValueError(f"No existe un archivo de base de datos: {path.name}")
    uri = path.as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        return conn
    except sqlite3.Error:
        try:
            conn.close()
        except (UnboundLocalError, sqlite3.Error):
            pass
        raise


def _pragma_rows(conn: sqlite3.Connection, pragma: str, name: str) -> list[sqlite3.Row]:
    return conn.execute(f"PRAGMA {pragma}({quote_identifier(name)})").fetchall()


def _describe_columns(conn: sqlite3.Connection, name: str) -> list[dict[str, Any]]:
    columns = []
    for row in _pragma_rows(conn, "table_info", name):
        columns.append({
            "cid": int(row["cid"]),
            "name": str(row["name"]),
            "type": str(row["type"] or ""),
            "not_null": bool(row["notnull"]),
            "default": row["dflt_value"],
            "pk_position": int(row["pk"]),
        })
    return columns


def inspect_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Enumera tablas, vistas, columnas, índices y claves foráneas."""
    objects = conn.execute(
        "SELECT type, name FROM sqlite_schema "
        "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY type, name"
    ).fetchall()
    inventory: dict[str, Any] = {"tables": {}, "views": {}}
    for obj in objects:
        object_type, name = str(obj["type"]), str(obj["name"])
        target = inventory["tables"] if object_type == "table" else inventory["views"]
        detail: dict[str, Any] = {"columns": _describe_columns(conn, name)}
        if object_type == "table":
            indexes = []
            for idx in _pragma_rows(conn, "index_list", name):
                idx_name = str(idx["name"])
                idx_columns = [
                    str(column["name"])
                    for column in _pragma_rows(conn, "index_info", idx_name)
                    if column["name"] is not None
                ]
                indexes.append({
                    "name": idx_name,
                    "unique": bool(idx["unique"]),
                    "origin": str(idx["origin"]),
                    "partial": bool(idx["partial"]),
                    "columns": idx_columns,
                })
            foreign_keys = []
            for fk in _pragma_rows(conn, "foreign_key_list", name):
                foreign_keys.append({
                    "id": int(fk["id"]),
                    "sequence": int(fk["seq"]),
                    "table": str(fk["table"]),
                    "from": str(fk["from"]),
                    "to": str(fk["to"] or ""),
                    "on_update": str(fk["on_update"]),
                    "on_delete": str(fk["on_delete"]),
                })
            detail["indexes"] = indexes
            detail["foreign_keys"] = foreign_keys
        target[name] = detail
    if not inventory["tables"] and not inventory["views"]:
        raise ValueError("La base SQLite no contiene tablas ni vistas de usuario.")
    return inventory


def list_user_tables(db_path: str | Path) -> list[str]:
    """Lista tablas de usuario mediante catálogo SQLite, sin leer sus filas."""
    conn = open_readonly_db(db_path)
    try:
        return sorted(inspect_schema(conn)["tables"])
    finally:
        conn.close()


def _metric(value: Any, scope: str, n_examined: int, method: str) -> dict[str, Any]:
    return {"value": value, "scope": scope, "n_examined": n_examined, "method": method}


def _numeric_or_temporal(declared_type: str) -> bool:
    kind = declared_type.upper()
    return any(token in kind for token in ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC", "DATE", "TIME"))


def _json_scalar(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _duplicate_row_count(
    conn: sqlite3.Connection,
    table_name: str,
    columns: list[dict[str, Any]],
    limit: int | None = None,
) -> int:
    """Cuenta copias de filas idénticas sin devolver valores de datos al proceso."""
    if not columns:
        return 0
    quoted_table = quote_identifier(table_name)
    quoted_columns = ", ".join(quote_identifier(column["name"]) for column in columns)
    source = f"SELECT {quoted_columns} FROM {quoted_table}"
    params: tuple[int, ...] = ()
    if limit is not None:
        source += " LIMIT ?"
        params = (limit,)
    query = (
        "WITH __audit_rows AS (" + source + ") "
        "SELECT COALESCE(SUM(duplicate_count - 1), 0) FROM ("
        f"SELECT COUNT(*) AS duplicate_count FROM __audit_rows GROUP BY {quoted_columns} "
        "HAVING COUNT(*) > 1)"
    )
    row = conn.execute(query, params).fetchone()
    return int(row[0] or 0)


def profile_schema(
    conn: sqlite3.Connection,
    inventory: dict[str, Any],
    *,
    depth: str,
    sample_rows: int = 100,
) -> dict[str, Any]:
    """Calcula métricas de muestra acotada o agregados de escaneo completo."""
    if depth not in {"basic", "deep"}:
        raise ValueError("La profundidad debe ser 'basic' o 'deep'.")
    if isinstance(sample_rows, bool) or not isinstance(sample_rows, int) or not 1 <= sample_rows <= MAX_SAMPLE_ROWS:
        raise ValueError(f"sample_rows debe estar entre 1 y {MAX_SAMPLE_ROWS}.")

    scope = "sample" if depth == "basic" else "full_scan"
    method = "first_rows_preview" if depth == "basic" else "sqlite_aggregate_scan"
    result: dict[str, Any] = {"tables": {}}

    for table_name, table in inventory["tables"].items():
        quoted_table = quote_identifier(table_name)
        columns = table["columns"]
        table_result: dict[str, Any] = {"columns": {}}
        if depth == "basic":
            for column in columns:
                column_name = column["name"]
                quoted_column = quote_identifier(column_name)
                values = conn.execute(
                    f"SELECT {quoted_column} FROM {quoted_table} LIMIT ?", (sample_rows,)
                ).fetchall()
                raw_values = [row[0] for row in values]
                n_examined = len(raw_values)
                non_null = [value for value in raw_values if value is not None]
                col_result = {
                    "null_count": _metric(n_examined - len(non_null), scope, n_examined, method),
                    "empty_string_count": _metric(
                        sum(isinstance(value, str) and value == "" for value in non_null),
                        scope, n_examined, method,
                    ),
                    "distinct_count": _metric(
                        len(set(non_null)), scope, n_examined, method,
                    ),
                }
                table_result["columns"][column_name] = col_result
            n_examined = max(
                (metric["n_examined"] for metrics in table_result["columns"].values()
                 for metric in metrics.values()),
                default=0,
            )
            if len(columns) <= 100:
                duplicate_rows = _duplicate_row_count(conn, table_name, columns, sample_rows)
                table_result["duplicate_rows"] = _metric(
                    duplicate_rows, scope, n_examined, method,
                )
            else:
                table_result["duplicate_rows"] = _metric(
                    None, "metadata", 0, "skipped_over_100_columns",
                )
            result["tables"][table_name] = table_result
            continue

        table_result["row_count"] = None
        chunks = [columns[offset:offset + 25] for offset in range(0, len(columns), 25)]
        if not chunks:
            chunks = [[]]
        for chunk_number, chunk in enumerate(chunks):
            expressions = ["COUNT(*) AS __audit_row_count"]
            aliases: dict[str, tuple[str, str]] = {}
            for index, column in enumerate(chunk):
                column_name = column["name"]
                qcol = quote_identifier(column_name)
                null_alias = f"__audit_null_{index}"
                empty_alias = f"__audit_empty_{index}"
                distinct_alias = f"__audit_distinct_{index}"
                expressions.append(f"SUM(CASE WHEN {qcol} IS NULL THEN 1 ELSE 0 END) AS {null_alias}")
                expressions.append(
                    f"SUM(CASE WHEN typeof({qcol})='text' AND {qcol}='' THEN 1 ELSE 0 END) AS {empty_alias}"
                )
                aliases[null_alias] = (column_name, "null_count")
                aliases[empty_alias] = (column_name, "empty_string_count")
                if "BLOB" not in str(column.get("type", "")).upper():
                    expressions.append(f"COUNT(DISTINCT {qcol}) AS {distinct_alias}")
                    aliases[distinct_alias] = (column_name, "distinct_count")
                if _numeric_or_temporal(str(column.get("type", ""))):
                    min_alias, max_alias = f"__audit_min_{index}", f"__audit_max_{index}"
                    expressions.extend((f"MIN({qcol}) AS {min_alias}", f"MAX({qcol}) AS {max_alias}"))
                    aliases[min_alias] = (column_name, "minimum")
                    aliases[max_alias] = (column_name, "maximum")
            row = conn.execute(
                f"SELECT {', '.join(expressions)} FROM {quoted_table}"
            ).fetchone()
            row_count = int(row["__audit_row_count"] or 0)
            table_result["row_count"] = _metric(row_count, scope, row_count, method)
            for alias, (column_name, metric_name) in aliases.items():
                value = row[alias]
                if metric_name in {"null_count", "empty_string_count", "distinct_count"}:
                    value = int(value or 0)
                else:
                    value = _json_scalar(value)
                table_result["columns"].setdefault(column_name, {})[metric_name] = _metric(
                    value, scope, row_count, method,
                )
        if len(columns) <= 100:
            duplicates = _duplicate_row_count(conn, table_name, columns)
            table_result["duplicate_rows"] = _metric(duplicates, scope, row_count, method)
        else:
            table_result["duplicate_rows"] = _metric(
                None, "metadata", 0, "skipped_over_100_columns",
            )
        result["tables"][table_name] = table_result
    return result


def _column_names(table: dict[str, Any]) -> list[str]:
    columns = table.get("columns", [])
    if isinstance(columns, dict):
        return [str(name) for name in columns]
    return [str(column.get("name", "")) if isinstance(column, dict) else str(column) for column in columns]


def _role_candidates(columns: list[str], aliases: list[str], role: str) -> list[dict[str, Any]]:
    scored = []
    for column in columns:
        normalized = _norm(column)
        score = max((_match_score(normalized, _norm(alias), role) for alias in aliases), default=0)
        if score > 0:
            scored.append({"column": column, "score": score})
    return sorted(scored, key=lambda item: (-item["score"], _norm(item["column"]), item["column"]))


def _confidence(candidates: list[dict[str, Any]]) -> tuple[str, float]:
    if not candidates:
        return "low", 0.0
    top = candidates[0]["score"]
    tied = sum(candidate["score"] == top for candidate in candidates) > 1
    if top == 4 and not tied:
        return "high", 1.0
    if top >= 2:
        return "medium", round(top / 4.0 * (0.8 if tied else 0.9), 2)
    return "low", round(top / 4.0 * 0.5, 2)


def _map_profile(columns: list[str], profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    roles: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    for role, aliases in profile["roles"].items():
        candidates = [candidate for candidate in _role_candidates(columns, aliases, role)
                      if candidate["column"] not in used]
        confidence_label, confidence_score = _confidence(candidates)
        selected = candidates[0] if candidates else None
        if selected:
            used.add(selected["column"])
        roles[role] = {
            "column": selected["column"] if selected else None,
            "score": confidence_score,
            "confidence_label": confidence_label,
            "source": "automatic",
            "alternatives": candidates[1:] if selected else [],
        }
    return roles


def _candidate_profile_score(profile_type: str, profile: dict[str, Any], roles: dict[str, dict[str, Any]], table_name: str) -> float | None:
    present = {role for role, mapping in roles.items() if mapping["column"] is not None}
    score = 2.0 if any(hint in _norm(table_name) for hint in profile["name_hints"]) else 0.0
    if profile_type == "tokens":
        need = sum(role in present for role in ("token_id", "token_name", "token_symbol", "created_at"))
        if "token_id" not in present or need < 2:
            return None
        score += 4 + need
    elif profile_type == "trades":
        need = sum(role in present for role in ("wallet", "side", "sol_amount", "token_amount", "ts"))
        if need < 3:
            return None
        score += 4 + need
    elif profile_type == "my_trades":
        has_pnl = bool(present.intersection({"pnl_sol", "pnl_pct", "win"}))
        has_entry = bool(present.intersection({"entry_time", "entry_price"}))
        has_exit = bool(present.intersection({"exit_time", "exit_price"}))
        if has_pnl and (has_entry or has_exit):
            score += 6
        elif has_entry and has_exit:
            score += 5
        else:
            return None
        if {"side", "wallet", "sol_amount"}.issubset(present) and not has_pnl:
            score -= 3
    elif profile_type == "ohlcv":
        if not {"close", "ts"}.issubset(present):
            return None
        score += 6
    score += 0.1 * len(present)
    return score


def map_semantics(inventory: dict[str, Any], mapping_override: dict[str, Any] | None = None) -> dict[str, Any]:
    """Asigna perfiles y roles con una confianza heurística y alternativas."""
    tables = inventory.get("tables", {})
    candidates: list[tuple[float, str, str, dict[str, dict[str, Any]]]] = []
    for table_name, table in tables.items():
        columns = _column_names(table)
        for profile_type, profile in TABLE_PROFILES.items():
            roles = _map_profile(columns, profile)
            score = _candidate_profile_score(profile_type, profile, roles, table_name)
            if score is not None:
                candidates.append((score, profile_type, table_name, roles))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    chosen: dict[str, Any] = {}
    used_tables: set[str] = set()
    used_profiles: set[str] = set()
    for score, profile_type, table_name, roles in candidates:
        if profile_type in used_profiles or table_name in used_tables:
            continue
        required_roles = [role for role in TABLE_PROFILES[profile_type]["key_roles"] if role != "pnl_or_prices"]
        present_required = sum(roles.get(role, {}).get("column") is not None for role in required_roles)
        exact_required = all(
            roles.get(role, {}).get("confidence_label") == "high"
            and not roles.get(role, {}).get("alternatives")
            for role in required_roles
        )
        confidence_label = (
            "high" if present_required == len(required_roles) and exact_required and score >= 7
            else "medium"
        )
        chosen[profile_type] = {
            "table": table_name,
            "score": round(score, 2),
            "confidence_label": confidence_label,
            "roles": roles,
            "missing_roles": [role for role in required_roles if roles.get(role, {}).get("column") is None],
        }
        used_tables.add(table_name)
        used_profiles.add(profile_type)

    override = {} if mapping_override is None else mapping_override
    if not isinstance(override, dict):
        raise ValueError("El mapping debe ser un objeto JSON por tipo de tabla.")
    for profile_type, spec in override.items():
        if profile_type not in TABLE_PROFILES or not isinstance(spec, dict):
            raise ValueError(f"Tipo de tabla inválido en mapping: {profile_type!r}.")
        table_name = spec.get("table") or chosen.get(profile_type, {}).get("table")
        if not table_name or table_name not in tables:
            raise ValueError(f"mapping.json: la tabla para '{profile_type}' no existe en la base.")
        columns = _column_names(tables[table_name])
        roles = _map_profile(columns, TABLE_PROFILES[profile_type])
        overrides = spec.get("columns", {})
        if not isinstance(overrides, dict):
            raise ValueError(f"mapping.json: 'columns' de '{profile_type}' debe ser un objeto.")
        for role, column_name in overrides.items():
            if role not in TABLE_PROFILES[profile_type]["roles"]:
                raise ValueError(f"mapping.json: rol desconocido '{role}' para '{profile_type}'.")
            if column_name not in columns:
                raise ValueError(f"mapping.json: la columna '{column_name}' no existe en '{table_name}'.")
            roles[role] = {
                "column": column_name,
                "score": 1.0,
                "confidence_label": "manual",
                "source": "manual",
                "alternatives": [],
            }
        chosen[profile_type] = {
            "table": table_name,
            "score": chosen.get(profile_type, {}).get("score", 0.0),
            "confidence_label": "manual",
            "roles": roles,
            "missing_roles": [
                role for role in TABLE_PROFILES[profile_type]["key_roles"]
                if role != "pnl_or_prices" and roles.get(role, {}).get("column") is None
            ],
            "source": "manual",
        }
    assigned_tables = {mapping["table"] for mapping in chosen.values()}
    missing_profiles = [profile_type for profile_type in TABLE_PROFILES if profile_type not in chosen]
    unclassified_tables = [table_name for table_name in tables if table_name not in assigned_tables]
    return {
        "tables": chosen,
        "unclassified_tables": unclassified_tables,
        "missing_profiles": missing_profiles,
    }


def _build_findings(
    profiles: dict[str, Any],
    semantics: dict[str, Any],
    data_origin: str,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if data_origin == "unknown":
        findings.append({
            "id": "provenance:unknown",
            "severity": "info",
            "category": "provenance",
            "scope": "metadata",
            "table": None,
            "message": "El origen de los datos no fue declarado.",
            "evidence": "source.origin=unknown",
            "recommended_action": "Declarar el origen conocido; el auditor no lo verifica de forma independiente.",
        })
    for table_name, table_profile in profiles.get("tables", {}).items():
        metric = table_profile.get("duplicate_rows", {})
        value = metric.get("value")
        if isinstance(value, int) and value > 0:
            findings.append({
                "id": f"duplicates:{table_name}",
                "severity": "warning",
                "category": "duplicate_rows",
                "scope": metric.get("scope", "metadata"),
                "table": table_name,
                "n_examined": metric.get("n_examined", 0),
                "message": f"Se observaron {value} filas duplicadas en '{table_name}'.",
                "evidence": f"duplicate_rows={value}; n_examined={metric.get('n_examined', 0)}",
                "recommended_action": "Revisar la clave de negocio y la estrategia de deduplicación; no se modificaron datos.",
            })
    for profile_type, mapping in semantics.get("tables", {}).items():
        missing_roles = mapping.get("missing_roles", [])
        if missing_roles:
            findings.append({
                "id": f"mapping:missing_roles:{profile_type}",
                "severity": "warning",
                "category": "semantic_mapping",
                "scope": "metadata",
                "table": mapping.get("table"),
                "message": f"El perfil '{profile_type}' no cubre todos sus roles clave.",
                "evidence": "missing_roles=" + ", ".join(missing_roles),
                "recommended_action": "Revisar aliases o definir un mapping manual y volver a auditar.",
            })
        for role, detail in mapping.get("roles", {}).items():
            if role in TABLE_PROFILES[profile_type]["key_roles"] and detail.get("alternatives"):
                alternatives = [item.get("column", "") for item in detail["alternatives"]]
                findings.append({
                    "id": f"mapping:ambiguous:{profile_type}:{role}",
                    "severity": "warning",
                    "category": "semantic_mapping",
                    "scope": "metadata",
                    "table": mapping.get("table"),
                    "message": f"Hay más de una columna candidata para el rol '{role}'.",
                    "evidence": "elegida=" + str(detail.get("column")) + "; alternativas=" + ", ".join(alternatives),
                    "recommended_action": "Confirmar el rol o especificarlo en --mapping; la heurística no es una probabilidad.",
                })
    if semantics.get("missing_profiles"):
        missing = semantics["missing_profiles"]
        findings.append({
            "id": "mapping:profiles_missing",
            "severity": "info",
            "category": "semantic_mapping",
            "scope": "metadata",
            "table": None,
            "message": "No se identificaron todos los perfiles de tabla conocidos.",
            "evidence": "missing_profiles=" + ", ".join(missing),
            "recommended_action": "Verificar el esquema y ampliar los perfiles solo con evidencia del dominio.",
        })
    return findings


def run_database_audit(
    db_path: str | Path,
    *,
    depth: str = "basic",
    data_origin: str = "unknown",
    sample_rows: int = 100,
    deep_confirmed: bool = False,
    mapping_path: str | None = None,
) -> dict[str, Any]:
    """Crea el modelo de reporte sin escribir en la base fuente."""
    if depth not in {"basic", "deep"}:
        raise ValueError("La profundidad debe ser 'basic' o 'deep'.")
    if depth == "deep" and not deep_confirmed:
        raise ValueError("El escaneo deep requiere confirmación explícita.")
    if data_origin not in VALID_ORIGINS:
        raise ValueError("data_origin debe ser unknown, synthetic o real.")
    path = Path(db_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise ValueError(f"No existe un archivo de base de datos: {path.name}")

    override = None
    if mapping_path:
        with open(mapping_path, "r", encoding="utf-8") as handle:
            override = json.load(handle)
        if not isinstance(override, dict):
            raise ValueError("mapping.json debe contener un objeto JSON por tipo de tabla.")
    conn: sqlite3.Connection | None = None
    try:
        conn = open_readonly_db(path)
        inventory = inspect_schema(conn)
        profiles = profile_schema(conn, inventory, depth=depth, sample_rows=sample_rows)
        semantics = map_semantics(inventory, override)
        schema_payload = json.dumps(
            inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        schema_sha256 = hashlib.sha256(schema_payload).hexdigest()
        warnings = []
        if data_origin == "unknown":
            warnings.append("El origen de los datos no está verificado; declare --data-origin si se conoce.")
        if semantics["unclassified_tables"]:
            warnings.append("Tablas sin clasificar: " + ", ".join(semantics["unclassified_tables"]))
        if semantics["missing_profiles"]:
            warnings.append("Perfiles ausentes: " + ", ".join(semantics["missing_profiles"]))
        findings = _build_findings(profiles, semantics, data_origin)
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "program_version": program_version,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "sqlite_version": sqlite3.sqlite_version,
            "source": {
                "filename": path.name,
                "size_bytes": path.stat().st_size,
                "origin": data_origin,
                "schema_sha256": schema_sha256,
            },
            "depth": depth,
            "sample_rows_limit": sample_rows if depth == "basic" else None,
            "inventory": inventory,
            "profiles": profiles,
            "semantic_mapping": semantics,
            "findings": findings,
            "warnings": warnings,
        }
    except sqlite3.DatabaseError as exc:
        raise ValueError("El archivo no es una base SQLite legible o cambió durante la auditoría.") from exc
    finally:
        if conn is not None:
            conn.close()
