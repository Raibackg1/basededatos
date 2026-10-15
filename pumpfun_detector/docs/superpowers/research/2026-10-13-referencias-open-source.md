# Referencias open source y técnicas

Fuentes consultadas para orientar el diseño del analizador. Las herramientas se usan como referencias de capacidades; esta fase no agrega dependencias externas.

1. **SQLite — EXPLAIN QUERY PLAN**: https://www.sqlite.org/eqp.html  
   Fuente primaria para interpretar si SQLite hace `SCAN` o `SEARCH`, qué índice usa y si recurre a estructuras temporales. Se aplicará en el subproyecto posterior de asesoría SQL; no se ejecutan planes ni recomendaciones de escritura en la fase actual.

2. **SQLite — ANALYZE y PRAGMA optimize**: https://www.sqlite.org/lang_analyze.html  
   SQLite documenta que estas operaciones recopilan estadísticas para el planificador y pueden cambiar planes de consulta. Por esa razón no se deben ejecutar en el auditor de solo lectura; cualquier uso posterior debe probarse en copia y evaluarse por separado.

3. **YData Profiling — conceptos y alertas**: https://docs.profiling.ydata.ai/latest/getting-started/concepts/  
   Referencia para perfiles univariados y multivariados, alertas de nulos, cardinalidad, desequilibrio, correlación y patrones temporales. La propia documentación señala que una alerta requiere validación del dominio; por eso las salidas del proyecto deben etiquetar evidencia y límites en vez de tratar heurísticas como hechos.

4. **Great Expectations — GX Core**: https://github.com/great-expectations/great_expectations  
   Referencia para expresar reglas de calidad como expectativas/test de datos y generar resultados comunicables. No se añade como dependencia en esta fase.

5. **SQLFluff**: https://github.com/sqlfluff/sqlfluff  
   Linter configurable por dialecto SQL; útil para análisis estático de formato y reglas. No sustituye el plan de ejecución real del motor.

6. **SQLGlot**: https://github.com/tobymao/sqlglot  
   Referencia para parseo y transformación de SQL entre dialectos. El análisis sintáctico no demuestra por sí mismo validez operativa ni buen rendimiento; el asesor posterior deberá corroborar con SQLite.

7. **scikit-learn — TimeSeriesSplit**: https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html  
   Referencia para mantener orden temporal entre entrenamiento y prueba y excluir un `gap` entre segmentos. Se usará como guía del diseño de validación de patrones posterior, no como requisito de dependencia para el auditor SQLite.
