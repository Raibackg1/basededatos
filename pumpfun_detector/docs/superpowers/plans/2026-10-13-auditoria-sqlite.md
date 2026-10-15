# Auditor SQLite seguro — Plan de implementación

> **Ejecución:** implementar tarea por tarea; cada tarea termina con una prueba verificable antes de avanzar.

**Objetivo:** agregar una ruta de auditoría SQLite de solo lectura que documente esquema, calidad y mapeos semánticos en JSON y HTML sin alterar datos ni reglas de trading.

**Arquitectura:** el modo CLI `--audit-only` llamará a un módulo independiente que abrirá SQLite mediante URI `mode=ro`, inspeccionará esquema y perfil de forma acotada, y construirá un reporte serializable. Un renderer separado escribirá JSON y HTML escapado, exclusivamente en `--out`. El camino auditor dependerá solo de la biblioteca estándar; las importaciones científicas se difieren al flujo de trading y el paquete preserva sus exportaciones públicas.

**Tecnologías:** Python y SQLite de la biblioteca estándar, más dependencias ya usadas; `unittest`; sin nuevas dependencias.

**Especificación:** `docs/superpowers/specs/2026-10-13-analizador-seguro-sqlite-design.md`

## Restricciones globales

- La base fuente se abre con `mode=ro` y una transacción de lectura; ninguna rutina ejecuta SQL de escritura.
- Importar `detector.database_audit` y `detector.audit_report` no requiere ni importa NumPy, pandas, SciPy, scikit-learn o Matplotlib.
- No se añaden dependencias.
- El modo básico usa una vista previa acotada a 100 primeras filas por tabla/columna; sus métricas siempre se etiquetan como `sample`, no como estadística poblacional.
- El modo profundo requiere terminal interactiva y confirmación afirmativa o `--confirm-deep`; en modo no interactivo sin flag debe detenerse antes del escaneo.
- `--demo` no puede combinarse con `--audit-only`; los datos ya generados se auditan con `--db ... --data-origin synthetic`.
- El origen (`unknown`, `synthetic`, `real`) es una declaración del usuario, no una verificación.
- No se cambian `MasterSnipeSignal`, los límites de riesgo, las reglas de producción ni los reportes actuales de trading.
- Como el checkout no tiene `.git`, no se crearán commits. El archivo `AGENTS.md` se corregirá para apuntar a pruebas existentes y nuevas.

## Estructura de archivos

- Crear `detector/database_audit.py`: conexión de solo lectura, inventario, perfil de calidad y mapeo con confianza.
- Crear `detector/audit_report.py`: serialización JSON, HTML autocontenido seguro y escritura atómica.
- Modificar `detector/__init__.py`: diferir imports pesados conservando `from detector import MasterSnipeSignal` y otras exportaciones existentes.
- Modificar `detector/inspector.py`: importar pandas dentro de `inspect_db`, no durante el import del paquete.
- Modificar `main.py`: enrutar auditoría antes de importar módulos científicos y preservar el flujo de trading.
- Modificar `AGENTS.md`: corregir la ruta de prueba obsoleta y añadir la suite nueva al comando obligatorio.
- Crear `tests/test_database_audit.py`: tests unitarios y de integración para lectura, esquema, perfil, mapeo, renderer y CLI.

## Interfaces que comparten las tareas

```python
# detector/database_audit.py
def quote_identifier(identifier: str) -> str: ...
def open_readonly_db(db_path: str | pathlib.Path) -> sqlite3.Connection: ...
def list_user_tables(db_path: str | pathlib.Path) -> list[str]: ...
def inspect_schema(conn: sqlite3.Connection) -> dict: ...
def profile_schema(conn: sqlite3.Connection, inventory: dict, *, depth: str,
                   sample_rows: int = 100) -> dict: ...
def map_semantics(inventory: dict, mapping_override: dict | None = None) -> dict: ...
def run_database_audit(db_path: str, *, depth: str = "basic",
                       data_origin: str = "unknown", sample_rows: int = 100,
                       deep_confirmed: bool = False,
                       mapping_path: str | None = None) -> dict: ...

# detector/audit_report.py
def render_audit_html(report: dict) -> str: ...
def write_audit_files(report: dict, output_dir: str) -> tuple[pathlib.Path, pathlib.Path]: ...
```

`run_database_audit` devuelve un diccionario con `schema_version`, `generated_at_utc`, `sqlite_version`, `source` (solo nombre de archivo, tamaño y origen declarado), `depth`, `inventory`, `profiles`, `semantic_mapping` y `warnings`. La versión inicial del esquema del reporte es `1`.

---

### Tarea 1: conexión SQLite de solo lectura e inventario

**Archivos:** crear `detector/database_audit.py`; crear `tests/test_database_audit.py`; modificar `detector/__init__.py` y `detector/inspector.py`.

**Interfaces:** produce `quote_identifier`, `open_readonly_db` e `inspect_schema`. `inspect_schema` lista tablas y vistas y, por tabla, columnas, tipos declarados, PK, índices e información de claves foráneas; no lee todas las filas. El import del paquete auditor y del inspector no importa bibliotecas científicas.

- [ ] **Paso 1: escribir tests que fallen**

Crear `tests/test_database_audit.py` con imports de `pathlib`, `sqlite3`, `tempfile`, `unittest` y las funciones bajo prueba. Añadir este helper compartido y usarlo en los tests de todas las tareas:

```python
def create_fixture_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE parent(token_id TEXT PRIMARY KEY, created_at TEXT);
        CREATE TABLE events(
            id INTEGER PRIMARY KEY,
            token_id TEXT REFERENCES parent(token_id),
            value TEXT
        );
        CREATE INDEX idx_events_token ON events(token_id);
        INSERT INTO parent VALUES ('mint-1', '2026-01-01T00:00:00Z');
        INSERT INTO events VALUES (1, 'mint-1', 'alpha');
        INSERT INTO events VALUES (2, 'mint-1', NULL);
        INSERT INTO events VALUES (3, 'mint-1', 'gamma');
        INSERT INTO events VALUES (4, 'mint-1', 'delta');
    """)
    conn.close()


class DatabaseAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = pathlib.Path(self.temp_dir.name) / "source.db"
        create_fixture_db(self.db_path)
        self.out_dir = pathlib.Path(self.temp_dir.name) / "out"

    def test_readonly_connection_rejects_write(self):
        conn = open_readonly_db(self.db_path)
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE forbidden(x)")
```

Agregar en la misma clase tests de `quote_identifier` con comillas/espacios y del inventario con PK, índice y FK. Añadir un subprocess test que ejecute `import detector.database_audit, detector.audit_report; import sys; assert not any(name in sys.modules for name in ('numpy', 'pandas', 'scipy', 'sklearn', 'matplotlib'))` y exigir código de salida `0`.

- [ ] **Paso 2: ejecutar los tests dirigidos y verificar que fallan**

Desde `pumpfun_detector`: `python -m unittest tests/test_database_audit.py -v`. Esperado: falla porque el módulo todavía no existe.

- [ ] **Paso 3: implementar el mínimo**

Usar `Path.resolve().as_uri() + "?mode=ro"` y `sqlite3.connect(..., uri=True)`, activar `PRAGMA query_only=ON` e iniciar una transacción de lectura. `quote_identifier` escapa cada `"` duplicándola. Interrogar `sqlite_schema`, `PRAGMA table_info`, `PRAGMA index_list`, `PRAGMA foreign_key_list` y `PRAGMA index_info` con los identificadores citados. Cerrar siempre la conexión en `run_database_audit` mediante `try/finally`. En `detector/__init__.py`, usar resolución diferida de atributos para mantener el API reexportado; mover `import pandas` dentro de `inspect_db`.

- [ ] **Paso 4: volver a ejecutar los tests**

`python -m unittest tests/test_database_audit.py -v`. Esperado: pasan lectura, quoting e inventario sin cambio en el hash/contenido de la fuente.

### Tarea 2: perfil básico/deep con procedencia y límites explícitos

**Archivos:** modificar `detector/database_audit.py`; ampliar `tests/test_database_audit.py`.

**Interfaces:** produce `profile_schema` y la validación de `depth`, `sample_rows`, `data_origin` y `deep_confirmed`.

- [ ] **Paso 1: escribir tests que fallen**

En `DatabaseAuditTests`, abrir la base de `setUp`, llamar `inspect_schema` y probar el perfil acotado:

```python
def test_basic_profile_is_labeled_as_sample(self):
    conn = open_readonly_db(self.db_path)
    self.addCleanup(conn.close)
    inventory = inspect_schema(conn)
    result = profile_schema(conn, inventory, depth="basic", sample_rows=3)
    metric = result["tables"]["events"]["columns"]["value"]["null_count"]
    self.assertEqual(metric["scope"], "sample")
    self.assertEqual(metric["n_examined"], 3)
```

Agregar tests de tabla con menos filas que el límite, base vacía y rechazo de `deep` no confirmado.

- [ ] **Paso 2: ejecutar los tests dirigidos y verificar que fallan**

`python -m unittest tests/test_database_audit.py -v`. Esperado: los casos de perfil profundo y `scope` fallan.

- [ ] **Paso 3: implementar el mínimo**

El modo básico consulta columnas una por una con `LIMIT ?`, procesa hasta 100 valores en memoria, calcula nulos/vacíos/cardinalidad observada y no guarda ni renderiza valores reales. Marca cada métrica con `scope="sample"`, `n_examined` y descripción `first_rows_preview`. El modo deep usa lecturas/agregados de tabla completa con lotes donde se iteren valores; `run_database_audit` rechaza `deep_confirmed=False` antes de recorrer filas. La conexión de solo lectura y la transacción son comunes a ambos modos.

- [ ] **Paso 4: volver a ejecutar los tests**

`python -m unittest tests/test_database_audit.py -v`. Esperado: muestra y profundidad aparecen correctamente etiquetadas y un deep sin confirmación no ejecuta perfilado.

### Tarea 3: mapeo semántico con confianza y override

**Archivos:** modificar `detector/database_audit.py`; ampliar `tests/test_database_audit.py`.

**Interfaces:** produce `map_semantics(inventory, mapping_override=None)` con rol, columna elegida, puntuación heurística, etiqueta de confianza, alternativas, campos ausentes y motivo. La puntuación no se presenta como probabilidad estadística.

- [ ] **Paso 1: escribir tests que fallen**

En `DatabaseAuditTests`, pasar un inventario mínimo con dos alias candidatos de igual fuerza:

```python
def test_mapping_reports_ambiguity_instead_of_hiding_it(self):
    inventory = {
        "tables": {
            "swaps": {"columns": ["token_id", "mint", "wallet", "side", "timestamp"]}
        },
        "views": {},
    }
    mapping = map_semantics(inventory)
    role = mapping["tables"]["trades"]["roles"]["token_id"]
    self.assertTrue(role["alternatives"])
    self.assertEqual(role["confidence_label"], "medium")
```

Agregar tests para coincidencia exacta alta, baja confianza, falta de roles y override manual validado contra nombres realmente existentes.

- [ ] **Paso 2: ejecutar los tests dirigidos y verificar que fallan**

`python -m unittest tests/test_database_audit.py -v`. Esperado: faltan alternativas, confianza y validación de override.

- [ ] **Paso 3: implementar el mínimo**

Reutilizar `TABLE_PROFILES`, `_norm` y `_match_score` de `detector.inspector`, sin cambiar el mapeo usado por el pipeline actual. Calcular candidatos alternativos por rol, conservar empates y etiquetar de forma heurística: coincidencia exacta única `high`, coincidencia no exacta o empate menor `medium`, coincidencia débil/override inválido `low` o error reportado. No asignar una tabla si no supera los requisitos estructurales mínimos del perfil.

- [ ] **Paso 4: volver a ejecutar los tests**

`python -m unittest tests/test_database_audit.py -v`. Esperado: los casos ambiguos quedan visibles y un override no existente nunca se aplica silenciosamente.

### Tarea 4: JSON versionado y HTML autocontenido seguro

**Archivos:** crear `detector/audit_report.py`; ampliar `tests/test_database_audit.py`.

**Interfaces:** produce `render_audit_html` y `write_audit_files`, que devuelven las rutas exactas de `auditoria.json` y `auditoria.html`.

- [ ] **Paso 1: escribir tests que fallen**

En `DatabaseAuditTests`, construir un reporte mínimo con nombre de tabla controlado por el fixture:

```python
def test_html_escapes_database_names(self):
    report = {
        "schema_version": 1,
        "generated_at_utc": "2026-10-13T00:00:00Z",
        "sqlite_version": sqlite3.sqlite_version,
        "source": {"filename": "source.db", "size_bytes": 1, "origin": "unknown"},
        "depth": "basic",
        "inventory": {"tables": {"<script>alert(1)</script>": {"columns": []}}, "views": {}},
        "profiles": {"tables": {}},
        "semantic_mapping": {"tables": {}},
        "warnings": [],
    }
    html = render_audit_html(report)
    self.assertNotIn("<script>alert(1)</script>", html)
    self.assertIn("&lt;script&gt;", html)
```

Agregar test de `schema_version=1`, origen declarado, `scope`, JSON válido, escritura atómica y error de salida no escribible.

- [ ] **Paso 2: ejecutar los tests dirigidos y verificar que fallan**

`python -m unittest tests/test_database_audit.py -v`. Esperado: no existen funciones de reporte.

- [ ] **Paso 3: implementar el mínimo**

Serializar JSON con UTF-8 y datos finitos; rechazar o normalizar NaN/Infinity. Renderizar HTML con `html.escape` para cada texto proveniente del esquema/datos y CSS/HTML inline. No incluir filas ni valores de muestra. Escribir primero a temporales dentro de `--out` y luego reemplazar los dos nombres finales solo al completar el reporte.

- [ ] **Paso 4: volver a ejecutar los tests**

`python -m unittest tests/test_database_audit.py -v`. Esperado: JSON parseable, HTML escapado y reporte previo no truncado ante error de escritura.

### Tarea 5: integrar CLI y actualizar la instrucción de pruebas

**Archivos:** modificar `main.py` y `AGENTS.md`; ampliar `tests/test_database_audit.py`.

**Interfaces:** flags `--audit-only`, `--audit-depth {basic,deep}`, `--confirm-deep`, `--data-origin {unknown,synthetic,real}`, `--audit-sample-rows` (predeterminado 100). Reutiliza `--db`, `--mapping` y `--out`.

- [ ] **Paso 1: escribir tests que fallen**

En `DatabaseAuditTests`, invocar el proceso desde la raíz del proyecto y usar `self.db_path` y `self.out_dir` creados en `setUp`:

```python
def test_audit_only_emits_both_reports_and_skips_trading(self):
    result = subprocess.run(
        [sys.executable, "main.py", "--db", str(self.db_path), "--audit-only",
         "--out", str(self.out_dir)],
        cwd=pathlib.Path(__file__).resolve().parents[1],
        capture_output=True, text=True,
    )
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertTrue((self.out_dir / "auditoria.json").exists())
    self.assertTrue((self.out_dir / "auditoria.html").exists())
    self.assertFalse((self.out_dir / "reporte.html").exists())
```

Agregar casos de `--demo --audit-only`, `deep` sin confirmación en modo no interactivo, archivo inexistente y origen sintético declarado.

- [ ] **Paso 2: ejecutar los tests de CLI y verificar que fallan**

`python -m unittest tests/test_database_audit.py -v`. Esperado: argparse aún no reconoce los flags nuevos.

- [ ] **Paso 3: integrar la bifurcación temprana**

En `main()` validar argumentos inmediatamente después de `parse_args()`. Si `--audit-only`, rechazar `--demo`, validar `--db`, obtener confirmación de deep antes de abrir tablas e importar solo `detector.database_audit`/`detector.audit_report`, llamar `run_database_audit` y `write_audit_files`, imprimir las rutas generadas y retornar `0`. Solo después de decidir que el modo es trading, importar NumPy/pandas y los módulos científicos actuales y llamar `setup_console_encoding`. Errores de entrada/configuración retornan no-cero con mensaje conciso. No ejecutar `generate_demo_db`, `load_data`, señales ni exportación de estrategia en el camino auditor.

Actualizar la sección de pruebas de `AGENTS.md` para apuntar a `python -m unittest discover -s tests -v`, que incluye la suite existente y la nueva; conservar la exigencia de código de salida `0`.

- [ ] **Paso 4: ejecutar pruebas de regresión**

`python -m unittest discover -s tests -v`. Esperado: todas las pruebas terminan con código `0`. Correr también CLI con la demo existente y `--data-origin synthetic`; comparar el hash del `.db` antes/después y verificar que los artefactos de trading no cambian.

## Revisión final

- [x] Confirmado que el checkout no tiene `.git`; no se creó commit.
- [x] Los tests de integración generan JSON/HTML en directorios temporales y verifican su contenido y seguridad.
- [x] No se añadieron dependencias; el modo auditor y las exportaciones de `MasterSnipeSignal` pasan comprobaciones independientes.
- [ ] Repetir la suite cuantitativa completa en un entorno con NumPy instalado; los dos módulos históricos no se pudieron importar en este equipo.


## Resultados de la ejecución (2026-10-13)

- Implementadas la CLI aislada, conexión SQLite `mode=ro`, inventario y perfilado de calidad, mapeo semántico con confianza/alternativas, hallazgos con evidencia/scope, renderer JSON/HTML escapado, README y referencias open source.
- Validación específica: **26 tests de auditoría pasan**; compilación Python y `python main.py --help` pasan sin NumPy/pandas.
- La integración sobre `demo_pumpfun.db` declara origen `synthetic`; un hash SHA-256 antes/después confirma que el origen no cambió.
- La suite completa se intentó: los tests nuevos pasan, pero `test_engine` y `test_optimizer_and_patterns` no llegan a ejecutarse porque el intérprete activo carece de NumPy. No se instalaron paquetes; repetir la suite completa en un entorno con las dependencias existentes.
- El checkout no contiene `.git`; no se creó commit.
