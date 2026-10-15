# Comparación P2 segura

- La base original no se modifica.
- Todo modelo queda `candidate_only`.

## Resultados

- Etiquetas on-chain generadas: `9`; bundle-like detectado: `True`.
- Meta-labeler: estado `candidate_only`, production_ready=`False`, train=80, test=35, OOS AUC=0.5340909090909091.
- Exportación CSV de control: `OK`.

## Interpretación

Las etiquetas amplían la observabilidad pero no prueban alpha. El meta-labeler demuestra un flujo temporal reproducible y permanece bloqueado para producción. DuckDB/Parquet depende de librerías opcionales; no se instalaron dependencias ni se alteró el entorno.
