# Diseño: auditor SQLite de solo lectura y calidad de datos

**Fecha:** 2026-10-13  
**Proyecto:** Pumpfun Detector  
**Estado:** aprobada e implementada (fase 1)
**Fase especificada:** auditoría SQLite segura (subproyecto 1 de 3)

## Decisión de producto

Extender el proyecto con una vía explícita de auditoría SQLite que funcione separada del análisis de trading. La auditoría permitirá entender qué contiene una base, detectar problemas de calidad y generar un informe verificable sin escribir en la base. No sustituirá `MasterSnipeSignal`, no cambiará reglas de producción y no ejecutará operaciones.

Se limitará la primera entrega a una base SQLite local proporcionada por el usuario. La documentación del proyecto refiere a una base canónica distinta, pero el archivo disponible en este checkout es `demo_pumpfun.db`; el propio generador describe patrones sintéticos intencionales. Por ello, toda salida debe indicar si los datos son demo o no verificados como reales y no presentar los hallazgos de demo como evidencia de rentabilidad real.

## Descomposición del alcance completo

Para mantener cada resultado comprobable, el trabajo se divide en tres subproyectos relacionados:

1. **Auditor SQLite seguro y perfilado de calidad (este diseño):** inventario, diagnóstico y reporte de una base en modo de solo lectura. Es la base para conocer datos y esquema antes de sacar conclusiones.
2. **Asesor de rendimiento SQL:** analizar consultas representativas con planes de SQLite, proponer índices u otros cambios y medir candidatos solo sobre copias de prueba. Se diseñará después de validar la auditoría.
3. **Validador reforzado de patrones/estrategias Pump.fun:** revisar fuga de datos, selección de hipótesis y resultados temporales fuera de muestra; separar hallazgos experimentales de reglas activas. Se diseñará como un subproyecto aparte porque modifica cómo se evalúa la evidencia cuantitativa.

La entrega actual cubre únicamente el subproyecto 1. No incluye la creación automática de índices ni el ajuste de parámetros de trading.

## Objetivos

- Abrir la base de origen en modo SQLite estrictamente de solo lectura.
- Mostrar tablas, columnas, tipos, claves, restricciones e índices detectados.
- Describir calidad y cobertura de datos con contadores que indiquen si son exactos, muestreados o no calculados.
- Identificar roles semánticos conocidos (tokens, operaciones, posiciones, OHLCV) con puntuación de confianza, evidencia y advertencias; no ocultar empates ni asignaciones dudosas.
- Producir un JSON versionado con versión del programa y huella SHA-256 del esquema, más un HTML autocontenido, separados del informe de trading actual.
- Permitir corrección explícita del mapeo con el archivo JSON existente, sin sobrescribirlo.

## No objetivos

- Ejecutar `CREATE`, `DROP`, `ALTER`, `UPDATE`, `INSERT`, `ANALYZE`, `VACUUM`, `PRAGMA optimize` ni ninguna otra operación que escriba en la base original.
- Optimizar o crear índices automáticamente.
- Reentrenar, publicar o reemplazar reglas de `MasterSnipeSignal`.
- Habilitar trading en vivo, enviar transacciones o cambiar límites de riesgo.
- Conectarse a servidores, redes o servicios externos; admitir otros motores SQL en esta fase.
- Presentar correlaciones, reglas de demo o puntuaciones heurísticas como prueba causal o garantía de rendimiento.
- Añadir dependencias de terceros en esta fase.

## Flujo de usuario

La CLI existente añadirá un modo de auditoría inequívoco que reutiliza `--db`, `--mapping` y `--out`, pero termina antes de cargar datos para la estrategia. El uso previsto será `python main.py --db ruta.db --audit-only --audit-depth basic --data-origin unknown --out resultados_auditoria`. `--audit-depth` acepta `basic` (predeterminado) o `deep`; `deep` requiere confirmación interactiva o el flag explícito `--confirm-deep`, y sin ninguno de los dos falla antes de leer las tablas completas. `--data-origin` acepta `unknown` (predeterminado), `synthetic` o `real`; es una declaración del usuario, no una verificación independiente. `--demo` y `--audit-only` son incompatibles, porque `--demo` puede regenerar la base. Para auditar la demo ya existente se usa `--db demo_pumpfun.db --audit-only --data-origin synthetic`.

El flujo será:

1. Validar que el archivo existe, sea SQLite legible y pueda abrirse en modo de solo lectura.
2. Obtener metadatos del catálogo SQLite y enumerar tablas/columnas/índices/relaciones sin seleccionar todos los datos.
3. Calcular un perfil básico limitado a una muestra acotada por tabla. El informe marcará el método y el tamaño de muestra; no se describirá como estadística poblacional.
4. En `deep`, mostrar antes en consola qué tablas se recorrerán y advertir que puede consumir tiempo e I/O. Requerir aceptación interactiva o `--confirm-deep`; permitir cancelación limpia.
5. Ejecutar el mapeo semántico actual, ampliando su resultado con confianza, alternativas y razones. Un override manual tiene prioridad y queda registrado como tal.
6. Escribir `auditoria.json` y `auditoria.html` en el directorio de salida. Si la carpeta no puede escribirse, la auditoría falla con un mensaje claro y no intenta escribir en la carpeta de la base.

## Componentes y límites

- **Orquestación CLI:** agrega `--audit-only` al flujo de `main.py`; valida opciones incompatibles y debe evitar importar módulos de trading y bibliotecas científicas en este modo. El modo auditor usa solo la biblioteca estándar de Python.
- **Inspector de solo lectura:** concentra la conexión SQLite y consultas al catálogo. Usa URI `mode=ro`, inicia una transacción de lectura para mantener una vista consistente durante el reporte, asegura cierre de conexión en todos los caminos y cita identificadores con una función dedicada. Valores de usuario se pasan como parámetros SQL; nunca se concatenan como valores.
- **Inventario/perfilado:** trabaja tabla por tabla y en lotes limitados. El modo básico no materializa tablas completas en memoria. El modo profundo separa el tiempo de cómputo y lo informa.
- **Mapeo semántico:** aprovecha los perfiles existentes, pero registra puntuación, candidatos competidores, roles ausentes y conflictos. Las ambigüedades producen advertencias; una asociación de baja confianza no se trata como confirmada.
- **Modelo del informe:** usa un objeto/diccionario serializable con versión de esquema, fuente del dato, metadatos de ejecución, inventario, métricas de calidad, mapeos y advertencias.
- **Renderizado:** JSON para consumo de herramientas y HTML autocontenido para revisión humana. El HTML escapa todo contenido originado en la base para evitar que nombres o valores se interpreten como código. No incluirá filas completas ni direcciones privadas por defecto.

## Métricas y reglas de interpretación

El perfil básico incluye número de columnas, tipo declarado, nulos/vacíos/cardinalidad observados y duplicados de filas dentro de una muestra acotada. No exporta valores de ejemplo. Cada métrica lleva `scope` (`sample`, `full_scan` o `metadata`), `n_examined`, y una nota sobre el método. Tablas con más de 100 columnas omiten el conteo de duplicados y lo indican como no calculado.

El modo profundo puede calcular, por columna, conteos exactos de nulos y cardinalidad cuando el tipo sea compatible, además de mínimos/máximos numéricos o temporales. Debe procesar datos en lotes y omitir métricas cuyo costo o tipo no sea seguro; no debe inferir zona horaria, moneda, unidades o clave primaria solo por el nombre de una columna. Estas inferencias aparecerán como hipótesis con evidencia y confianza, no como hechos.

Los hallazgos se estructuran por severidad (advertencia o información), categoría, evidencia, alcance, tabla afectada y una acción sugerida que no modifique el origen. No se emiten errores de calidad con umbrales genéricos de dominio. El HTML escapa también mensajes/evidencias generados a partir de nombres de esquema.

## Errores y consistencia

- Archivo inexistente, no SQLite, cifrado/no legible, permisos insuficientes o esquema vacío: detener el proceso con error claro y código de salida distinto de cero.
- Tabla o columna con nombre inusual: tratarla como identificador citado, no interpolarla sin escape.
- Mapeo con empate o campos necesarios ausentes: informar el conflicto y continuar con secciones independientes que sí sean válidas; no inventar una relación.
- Tabla que cambia durante la auditoría: detectar errores de lectura, marcar el informe incompleto y no producirlo como completo.
- Interrupción del usuario: cerrar conexión y no dejar un JSON/HTML que parezca completo; los temporales, si existen, se reemplazan de forma atómica al final.
- El informe registra versión del programa, versión SQLite, fecha UTC, nombre de archivo (sin ruta absoluta por defecto), tamaño del archivo, huella SHA-256 del esquema, configuración de perfilado y origen declarado (`unknown`, `synthetic` o `real`). La huella cubre el inventario/schema, no el contenido completo del archivo. El origen se identifica como declarado, no verificado. No incluirá secretos ni muestras sensibles por defecto.

## Compatibilidad y dependencias

- Mantener compatibilidad con la CLI actual de Python y las bibliotecas ya usadas en el proyecto.
- El modo `--audit-only` debe poder importarse y ejecutarse sin NumPy, pandas, SciPy, scikit-learn ni Matplotlib; las importaciones pesadas del paquete/CLI deben diferirse sin cambiar las exportaciones públicas existentes.
- No añadir paquetes nuevos en esta fase. Toda necesidad futura de un parser SQL externo se justificará por separado.
- La política del proyecto exige tests Python; el árbol disponible contiene `tests/test_engine.py`, pero no se encontró el archivo `scratch/test_advanced_engine.py` indicado por el `AGENTS.md`. El plan de implementación debe resolver esta discrepancia y conservar la suite existente.

## Pruebas y criterios de aceptación

1. Un test crea una base pequeña en un directorio temporal, ejecuta auditoría y comprueba tablas, columnas, claves e índices conocidos.
2. Un test verifica el modo de solo lectura intentando ejecutar internamente una operación de escritura: debe rechazarse y el hash/contenido lógico de la base debe permanecer intacto.
3. Tests cubren tablas y columnas con comillas, espacios, palabras reservadas, base vacía, archivo no SQLite y override manual de mapeo.
4. Tests prueban que los perfiles de muestra informan `scope=sample`, `n_examined` correcto y no anuncian resultados exactos para toda la tabla.
5. Tests cubren empates y mapeos de confianza baja; no se promueven en silencio a roles confirmados.
6. HTML escapa los valores que contengan etiquetas o scripts y no presenta filas sin solicitarlo.
7. Un test de integración con `demo_pumpfun.db` confirma inventario y reporta explícitamente `synthetic` cuando se pasa `--data-origin synthetic`; no convierte sus patrones intencionales en validación real. Un test adicional confirma que `--demo --audit-only` se rechaza antes de regenerar o modificar la base.
8. El modo de auditoría no genera ni modifica `resultados/reporte.html`, `reglas_produccion.json`, `estrategia_produccion.py` ni la base de entrada.
9. La suite existente termina con código 0 y la nueva suite cubre el módulo de auditoría.

## Seguridad, permisos y operación

La auditoría local no tiene efectos externos. El archivo de entrada se abre solo para lectura y todos los artefactos se guardan en `--out`. El comportamiento por defecto no exporta filas ni consulta una ruta distinta a la proporcionada. El sistema nunca cambia la política del bot ni produce una señal ejecutable como efecto de la auditoría.

Las referencias SQLite y open source revisadas, con la decisión que respaldan, están conservadas en [`docs/superpowers/research/2026-10-13-referencias-open-source.md`](../research/2026-10-13-referencias-open-source.md).

## Riesgos y mitigaciones

- **La muestra inicial está sesgada:** etiquetar la cobertura con claridad como vista previa acotada de las primeras filas, no como muestra aleatoria ni representativa, y reservar los agregados de tabla completa al modo profundo explícito.
- **El mapeo automático asigna roles incorrectos:** mostrar confianza y alternativas, admitir override y nunca presentar baja confianza como confirmada.
- **Los nombres o contenidos pueden ser maliciosos:** modo de solo lectura, quoting seguro de identificadores y escape HTML.
- **Los conteos completos pueden ser costosos:** no hacer full scan de manera silenciosa; mostrar las tablas afectadas y permitir cancelación.
- **Se confunde auditoría con optimización de trading:** comandos y reportes separados; ninguna actualización automática de reglas.

## Decisiones pendientes para subproyectos posteriores

El asesor de rendimiento SQL definirá una fuente de consultas representativas, cómo medir tiempos y cómo probar índices en una copia aislada. El validador de patrones definirá el tamaño mínimo de muestra, métricas de incertidumbre, ventanas temporales, embargo y los criterios de promoción. Esas decisiones no bloquean la auditoría de solo lectura y se revisarán en sus propias especificaciones antes de implementar dichos subproyectos.


## Estado de implementación y validación

La fase 1 quedó implementada en `main.py`, `detector/database_audit.py` y `detector/audit_report.py`, con el README actualizado. Se validaron **26/26 tests de auditoría** y `py_compile`; el test de integración auditó `demo_pumpfun.db` como `synthetic` y confirmó que su SHA-256 no cambia. `--help` funciona sin importar NumPy/pandas.

La suite completa (`python -m unittest discover -s tests -v`) ejecuta los tests nuevos pero no puede importar dos suites históricas (`tests/test_engine.py` y `tests/test_optimizer_and_patterns.py`), porque el Python disponible en este equipo no tiene NumPy. No se instalaron dependencias. Por ello, la regresión completa del flujo cuantitativo existente queda pendiente de un entorno con las dependencias ya instaladas; este bloqueo no afecta a la auditoría, que usa solo biblioteca estándar.
