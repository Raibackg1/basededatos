# Diseño P2 segura

## Objetivo
Ampliar la medición sin convertir experimentos en órdenes reales ni promover modelos automáticamente.

## Componentes
1. `onchain_risk.py`: calcula etiquetas descriptivas de creator concentration, liquidity depth, migration progress y bundle-like flow a partir de snapshots disponibles en entrada. No decide ni usa outcomes futuros.
2. `meta_labeler.py`: meta-labeling reproducible con regresión logística opcional de scikit-learn, splits temporales con purge gap, métricas OOS y estado `candidate_only`.
3. `columnar_export.py`: exporta snapshots a Parquet/DuckDB solo si las dependencias opcionales están instaladas; nunca muta la fuente y devuelve diagnóstico explícito si faltan.
4. `paper_adapters.py`: adaptadores de señal/quote/submitter para replay y paper; el submitter real no se crea en P2.

## Guardas
- No se conectan secretos ni APIs de trading.
- No se generan órdenes on-chain.
- Features con prefijo `future_`, `pnl_`, `exit_`, `mfe_`, `mae_` quedan fuera.
- Un modelo P2 solo puede producir `candidate_only=True` hasta tener validación OOS y forward_live.

## Criterio de aceptación
Tests unitarios, compilación, benchmark P2 y reporte que separe utilidad técnica de rentabilidad real.
