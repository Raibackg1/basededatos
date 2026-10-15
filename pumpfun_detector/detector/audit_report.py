"""Renderizado local de los informes de auditoría SQLite."""
from __future__ import annotations

import html
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def render_audit_html(report: dict[str, Any]) -> str:
    source = report.get("source", {})
    inventory = report.get("inventory", {})
    profiles = report.get("profiles", {}).get("tables", {})
    mappings = report.get("semantic_mapping", {}).get("tables", {})
    findings = report.get("findings", [])
    warnings = report.get("warnings", [])

    sections = [
        "<!doctype html>",
        '<html lang="es"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Auditoría SQLite</title>",
        "<style>body{font:15px system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#172033;background:#f7f8fb}"
        "h1,h2{color:#112244}section{background:#fff;padding:1rem 1.25rem;margin:1rem 0;border:1px solid #d9deea;border-radius:10px}"
        "table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:.5rem;border-bottom:1px solid #e3e7ef}"
        ".finding{padding:.5rem 1rem;margin:.75rem 0;background:#fff;border-left:4px solid #526079}"
        ".warning{border-color:#d97706;background:#fff8e8}.info{border-color:#2563eb;background:#eff6ff}"
        ".error{border-color:#dc2626;background:#fff1f2}.muted{color:#526079}</style>",
        "</head><body>",
        "<h1>Auditoría de base SQLite</h1>",
        "<p class='muted'>Solo lectura · informe local · no modifica reglas de trading</p>",
        "<section><h2>Resumen</h2><ul>",
        f"<li>Archivo: <code>{_escape(source.get('filename', 'desconocido'))}</code></li>",
        f"<li>Origen declarado: <strong>{_escape(source.get('origin', 'unknown'))}</strong> (no verificado)</li>",
        f"<li>Versión del programa: {_escape(report.get('program_version', ''))}</li>",
        f"<li>Profundidad: {_escape(report.get('depth', 'basic'))}</li>",
        f"<li>Huella SHA-256 del esquema: <code>{_escape(source.get('schema_sha256', ''))}</code></li>",
        f"<li>Generado (UTC): {_escape(report.get('generated_at_utc', ''))}</li>",
        f"<li>Versión del reporte: {_escape(report.get('schema_version', ''))}</li>",
        "</ul></section>",
        "<section><h2>Esquema</h2>",
    ]
    for table_name, table in inventory.get("tables", {}).items():
        sections.append(f"<h3>Tabla: <code>{_escape(table_name)}</code></h3>")
        sections.append(
            f"<table><caption>Esquema de la tabla {_escape(table_name)}</caption>"
            "<thead><tr>"
            '<th scope="col">Columna</th><th scope="col">Tipo</th>'
            '<th scope="col">NOT NULL</th><th scope="col">PK</th>'
            "</tr></thead><tbody>")
        for column in table.get("columns", []):
            sections.append(
                "<tr>"
                f"<td>{_escape(column.get('name', ''))}</td>"
                f"<td>{_escape(column.get('type', ''))}</td>"
                f"<td>{'sí' if column.get('not_null') else 'no'}</td>"
                f"<td>{_escape(column.get('pk_position', 0))}</td>"
                "</tr>"
            )
        sections.append("</tbody></table>")
        table_profile = profiles.get(table_name, {})
        if table_profile.get("row_count"):
            metric = table_profile["row_count"]
            sections.append(
                f"<p>Filas: {_escape(metric.get('value'))} ({_escape(metric.get('scope'))})</p>"
            )
        sections.append("<p>Índices: " + _escape(", ".join(i["name"] for i in table.get("indexes", []))) + "</p>")
        for fk in table.get("foreign_keys", []):
            sections.append(
                f"<p>Relación: {_escape(fk.get('from'))} → "
                f"{_escape(fk.get('table'))}.{_escape(fk.get('to'))}</p>"
            )
    for view_name, view in inventory.get("views", {}).items():
        sections.append(f"<p>Vista: <code>{_escape(view_name)}</code> — {_escape(len(view.get('columns', [])))} columnas</p>")
    sections.append("</section><section><h2>Calidad de datos</h2>")
    if not profiles:
        sections.append("<p>No se calcularon métricas.</p>")
    for table_name, table_profile in profiles.items():
        sections.append(
            f"<h3>{_escape(table_name)}</h3>"
            f"<table><caption>Calidad de datos de {_escape(table_name)}</caption>"
            "<thead><tr>"
            '<th scope="col">Columna</th><th scope="col">Métrica</th>'
            '<th scope="col">Valor</th><th scope="col">Alcance</th>'
            '<th scope="col">Filas examinadas</th>'
            "</tr></thead><tbody>")
        for column_name, metrics in table_profile.get("columns", {}).items():
            for metric_name, metric in metrics.items():
                sections.append(
                    "<tr>"
                    f"<td>{_escape(column_name)}</td><td>{_escape(metric_name)}</td>"
                    f"<td>{_escape(metric.get('value'))}</td><td>{_escape(metric.get('scope'))}</td>"
                    f"<td>{_escape(metric.get('n_examined'))}</td></tr>"
                )
        sections.append("</tbody></table>")
        duplicate_metric = table_profile.get("duplicate_rows")
        if duplicate_metric:
            sections.append(
                f"<p>Filas duplicadas observadas: {_escape(duplicate_metric.get('value'))} "
                f"({_escape(duplicate_metric.get('scope'))}; "
                f"{_escape(duplicate_metric.get('n_examined'))} filas examinadas)</p>"
            )
    sections.append("</section><section><h2>Mapeo semántico</h2>")
    if not mappings:
        sections.append("<p>No se confirmó ningún perfil semántico.</p>")
    for profile_type, mapping in mappings.items():
        sections.append(
            f"<h3>{_escape(profile_type)} → <code>{_escape(mapping.get('table'))}</code> "
            f"({_escape(mapping.get('confidence_label'))})</h3><ul>"
        )
        for role, detail in mapping.get("roles", {}).items():
            column = detail.get("column")
            alternatives = ", ".join(item.get("column", "") for item in detail.get("alternatives", []))
            note = f"; alternativas: {alternatives}" if alternatives else ""
            sections.append(
                f"<li>{_escape(role)}: {_escape(column or 'sin asignar')} "
                f"({_escape(detail.get('confidence_label'))}){_escape(note)}</li>"
            )
        sections.append("</ul>")
    sections.append("</section><section><h2>Hallazgos explicables</h2>")
    if not findings:
        sections.append("<p>Sin hallazgos generados por las reglas actuales.</p>")
    for finding in findings:
        severity = finding.get("severity", "info")
        severity_class = severity if severity in {"info", "warning", "error"} else "info"
        sections.append(
            f"<article class='finding {_escape(severity_class)}'>"
            f"<h3>{_escape(finding.get('severity', 'info'))}: {_escape(finding.get('category', ''))}</h3>"
            f"<p>{_escape(finding.get('message', ''))}</p>"
            f"<p><strong>Evidencia:</strong> {_escape(finding.get('evidence', ''))}; "
            f"<strong>alcance:</strong> {_escape(finding.get('scope', 'metadata'))}</p>"
            f"<p><strong>Acción sugerida:</strong> {_escape(finding.get('recommended_action', ''))}</p>"
            "</article>"
        )
    sections.append("</section><section><h2>Notas</h2>")
    if warnings:
        for warning in warnings:
            sections.append(f"<p class='warning'>{_escape(warning)}</p>")
    else:
        sections.append("<p>Sin advertencias.</p>")
    sections.extend(["</section>", "<footer class='muted'>El origen de los datos es una declaración del usuario. Este informe no valida rentabilidad.</footer>", "</body></html>"])
    return "\n".join(sections)


def _write_temp(path: Path, content: str) -> Path:
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return temp_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def write_audit_files(report: dict[str, Any], output_dir: str | os.PathLike[str]) -> tuple[Path, Path]:
    """Escribe ambos artefactos en el directorio de salida, de forma atómica por archivo."""
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "auditoria.json"
    html_path = destination / "auditoria.html"
    json_text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    html_text = render_audit_html(report) + "\n"
    staged: list[tuple[Path, Path]] = []
    try:
        staged = [(_write_temp(json_path, json_text), json_path), (_write_temp(html_path, html_text), html_path)]
        for temp_path, final_path in staged:
            os.replace(temp_path, final_path)
    finally:
        for temp_path, _ in staged:
            temp_path.unlink(missing_ok=True)
    return json_path, html_path
