# Especificación de diseño: recolector live Pump.fun con Helius

**Fecha:** 2026-10-14
**Estado:** diseño aprobado por secciones; pendiente de revisión del documento antes de planificar/implementar
**Ámbito:** captura continua de actividad on-chain Pump.fun/PumpSwap para análisis; no ejecución de trading.

## Objetivo

Añadir un recolector de solo lectura que ingiera eventos Pump.fun y PumpSwap en tiempo real, conserve transacciones y trades normalizados en una SQLite paralela y alimente el pipeline existente con snapshots verificables. La primera corrida del pipeline se habilita al persistir al menos 1.001 trades válidos, únicos y finalizados. El recolector continúa después de ese primer análisis, sujeto a topes de uso y almacenamiento.

El origen `custom.db` no se abre ni modifica. No se requiere exportación paga de Dune: el universo de tokens proviene de las transacciones vistas en vivo. El CSV de trades es una exportación de análisis; SQLite es la fuente de verdad.

## No objetivos

- No descargar el historial completo de Pump.fun desde el génesis de la cadena. La captura empieza al iniciar el servicio; la recuperación cubre solamente firmas disponibles desde el último checkpoint.
- No comprar, vender, firmar transacciones, usar una wallet ni conectar el bot de ejecución.
- No pagar PumpPortal por suscripciones de trades ni adquirir créditos Helius. Ante topes o falta de cuota, el servicio se pausa.
- No alterar `D:\hjh\pumpscope\db\custom.db` ni las bases demo.
- No inferir métricas ausentes —por ejemplo holders, USD o balances del dev— a partir de logs que no las contienen.
- No registrar una tarea de inicio automático de Windows. El proceso solo queda activo mientras esta sesión/dispositivo permanezca encendido y conectado.

## Alternativas evaluadas

### Recomendada: Helius LaserStream WebSocket con `logsSubscribe`

La guía de Helius documenta una suscripción de logs para el programa Pump AMM disponible en todos los planes, y recupera la transacción completa mediante `getTransaction`. El plan Free publica 1 millón de créditos y 10 solicitudes/s. La implementación debe contar solicitudes y detenerse dentro de límites locales conservadores; el uso de la misma clave por otras aplicaciones consume la cuota compartida. Esta alternativa evita el cargo en SOL por mensaje de PumpPortal y no necesita CSV de mints.

### No recomendada: PumpPortal Data API para trades

PumpPortal ofrece gratis eventos de creación y migración, pero las suscripciones de trades tienen un cargo publicado de 0,01 SOL por cada 10.000 mensajes y requieren API key con wallet vinculada y saldo mínimo de 0,02 SOL. Se descarta para no habilitar cargos cripto. Puede mantenerse como alternativa futura si el usuario aprueba expresamente ese costo.

### Solo respaldo/pruebas: RPC público de Solana

El endpoint público puede servir para pruebas, pero Solana advierte que sus límites pueden cambiar y que no está pensado para producción. La ruta principal usa el endpoint Helius del plan Free y limita las llamadas.

## Arquitectura

### Componentes

1. **`scripts/collect_helius_live.py`**: proceso CLI de captura, reconexión, cuotas, persistencia de firmas y creación de snapshots.
2. **`detector/helius_live_decoder.py`**: decoder puro, sin red, que recibe una respuesta RPC y produce cero o más eventos validados. Utiliza IDL oficiales de Pump.fun y PumpSwap fijados por versión/hash en el código y deja registrada esa versión en la base.
3. **SQLite paralela**: `outputs/live_pumpfun.sqlite`, separada de cualquier base canónica o demo.
4. **Exportación**: CSV UTF-8 derivado de `trades`, en `outputs/live_pumpfun/trades.csv`, actualizado al generar cada snapshot.
5. **Pipeline**: `main.py` analiza una copia consistente de la SQLite, no el archivo activo que recibe escrituras. Se ejecuta con `--data-origin real` y sin `--optimize-db`.

La implementación reutilizará `websockets` y las convenciones existentes cuando estén instaladas. No añadirá dependencias sin justificación y revisión, conforme a las instrucciones del repositorio.

### Flujo de datos

1. Leer `HELIUS_API_KEY` del entorno del proceso. No aceptar la clave por argumento CLI, archivo, URL impresa, log ni salida de error.
2. Abrir `wss://mainnet.helius-rpc.com/?api-key=…`, enviar ping aproximadamente cada 60 segundos y establecer dos filtros `logsSubscribe` al nivel `finalized`: uno para el programa Pump.fun y otro para PumpSwap. La API `mentions` filtra una dirección por suscripción; ambos filtros comparten la misma conexión.
3. Deduplicar firmas recibidas por los dos filtros. Guardar cada firma pendiente en SQLite antes de solicitarla, para que un reinicio no la pierda.
4. Recuperar la transacción con `getTransaction`. Reintentar respuestas temporalmente ausentes, `429` y errores de red con backoff exponencial acotado y respetar `Retry-After` cuando exista.
5. Decodificar eventos usando los IDL fijados. Solo transacciones exitosas generan trades. La clave idempotente del trade es `(tx_signature, event_index)`, ya que una transacción puede producir más de un evento. Registrar por separado `instruction_index` cuando pueda determinarse.
6. Aceptar en `trades` únicamente trades con cotización en SOL o wrapped SOL y campos de tiempo, mint, lado y cantidades interpretables. Guardar en `raw_transactions` el JSON íntegro de toda respuesta candidata, incluidas transacciones con cotización no soportada o eventos no decodificables, junto con su `parse_status`. Así los casos excluidos quedan auditables y no contaminan el análisis SOL con unidades de otra moneda.
7. Poblar `tokens` con eventos de creación observados. Los metadatos ausentes permanecen `NULL`; no se fabrican.
8. Crear el primer snapshot y lanzar el pipeline al alcanzar 1.001 filas válidas en `trades`. Después, ejecutar como máximo un análisis cada 24 horas y solo si se añadieron al menos 1.001 trades desde el último análisis exitoso. La captura permanece activa mientras haya cuota y almacenamiento.
9. Crear la copia de análisis con la API de backup de SQLite, exportar el CSV y ejecutar el pipeline sobre la copia. No aplicar índices/PRAGMAs automáticamente a la base activa.

## Esquema de almacenamiento

### `tokens`

Una fila por mint, con columnas compatibles con el inspector: `mint` como clave primaria; `name`, `symbol`, `created_at`, `dev_wallet`, `twitter`, `telegram`, `website`, `total_supply` y `complete`. Los campos no presentes en el evento de creación quedan nulos.

### `trades`

Una fila por evento de trade elegible, con el núcleo de columnas que ya entiende el loader: `token_mint`, `wallet`, `side`, `sol_amount`, `token_amount`, `price`, `block_time` y `tx_signature`. Añadir `slot`, `program_id`, `instruction_index`, `event_index`, `quote_mint`, `sol_lamports`, `token_amount_raw`, `token_decimals` y `parser_version` para trazabilidad y precisión. `block_time` será ISO-8601 UTC; las cantidades de UI se derivan de unidades atómicas y se conservan los enteros originales. Índices: `(token_mint, block_time)`, `(wallet, block_time)` y firma. Restricción única `(tx_signature, event_index)`.

La tabla conserva los nombres canónicos usados por el pipeline actual; las columnas adicionales no alteran el mapeo semántico. Se evita la restricción histórica `(token_mint, tx_signature)`, que puede colapsar eventos distintos de una misma transacción.

### `raw_transactions`

Una fila por firma candidata: `signature` primaria; `slot`, `block_time`, `received_at`, `program_ids`, `raw_json`, `parse_status`, `parse_error` e `idl_hash`. El JSON permite auditar o redecodificar sin otra consulta Helius. El mensaje de error se sanitiza para excluir URL/API key.

### `collector_state`

Una fila por programa monitorizado: firma/slot del último evento procesado contiguamente, última actualización, estado de conexión, número de reintentos y límites de uso. La cola persistente de firmas pendientes conserva firmas aún no procesadas. Avanzar el checkpoint solo después de guardar resultado o error terminal auditado.

### SQLite y CSV

Activar WAL, `busy_timeout` y commits por lotes acotados. No borrar eventos automáticamente. El CSV UTF-8 se exporta desde `trades` con las columnas canónicas primero y metadatos después; su encabezado y formato quedan estables. CSV no reemplaza el archivo SQLite ni se usa para la cola/checkpoint.

## Límites operativos y seguridad

- **Finalidad:** `finalized`, priorizando consistencia de investigación sobre latencia de trading.
- **Ritmo:** máximo local de 8 llamadas HTTP RPC/s, por debajo de las 10 solicitudes/s publicadas para Helius Free.
- **Cuota local:** máximo 5.000 recuperaciones de transacción por día y 150.000 solicitudes HTTP RPC por periodo móvil de 30 días, incluyendo recuperación de huecos. La cuota Free publicada es 1 millón de créditos; la clave debe ser dedicada al recolector. Si Helius informa cuota agotada, pausar inmediatamente. No asumir que la clave está libre de uso por otros sistemas.
- **Almacenamiento:** pausa de seguridad cuando el total de `outputs/live_pumpfun/` alcance 2 GB, contando SQLite, WAL/SHM, snapshots, CSV y reportes. No borra ni purga artefactos; elevar el límite o archivar requiere acción explícita del usuario.
- **Reintentos:** backoff exponencial con jitter, máximo 60 segundos. Ante error de autorización, configuración inválida o cuota agotada, detener el bucle de reintento y registrar estado de pausa.
- **Recuperación de huecos:** al reconectar, consultar `getSignaturesForAddress` desde el checkpoint de cada programa, paginar y procesar firmas no vistas. El costo de recuperación cuenta contra los mismos topes. Si las firmas ya no están disponibles, registrar el intervalo como hueco y no declarar cobertura completa.
- **Secreto:** rotar la clave compartida en el chat y configurar una nueva solo como variable de entorno del proceso. Nunca escribirla en la SQLite, `.env`, archivos de configuración, argumentos o logs.
- **Ejecución:** modo de captura solamente; ninguna transacción firmada, wallet o llamada de compra/venta. El proceso no se registra en inicio de Windows; apagar/suspender el Desktop interrumpe el stream, que se recupera con checkpoints al volver a iniciarlo.

## Pipeline y resultados

Al alcanzar 1.001 trades elegibles, usar una copia SQLite consistente como entrada:

```powershell
python main.py --db outputs\live_pumpfun\snapshot.sqlite --out outputs\live_pumpfun\analysis --data-origin real
```

No añadir `--optimize-db`. El pipeline generará sus CSV/reporte en el directorio indicado. El recolector no afirmará que 1.001 eventos garantizan 20 etiquetas válidas para todas las métricas: si faltan horizontes futuros o muestras, se conservan las advertencias del pipeline y el siguiente snapshot se evalúa cuando maduren nuevos datos.

## Manejo de fallos y observabilidad

- Registrar estado de conexión, último slot, firmas pendientes, transacciones obtenidas, trades insertados, filas excluidas por parse/quote, llamadas RPC acumuladas, tamaño de DB y último error sanitizado.
- Reintentar fallos transitorios sin duplicar escrituras. Un `signature` ya persistido no provoca una segunda consulta salvo que el resultado anterior esté marcado como temporalmente incompleto.
- Los casos de decodificación desconocida quedan en `raw_transactions` con `parse_status='unsupported'` para revisión; nunca se reinterpretan como trades por heurística.
- Si el proceso alcanza un límite, conserva la cola y se pausa en vez de descartar mensajes o comprar cuota.
- En la primera versión, el control operacional es por CLI/log y fichero SQLite. No se añade dashboard ni integración externa.

## Pruebas y criterios de aceptación

1. Pruebas unitarias offline de decodificación para compras/ventas Pump.fun y PumpSwap, eventos múltiples por transacción, cotización SOL/wSOL, cotización no soportada, timestamps ausentes y errores de transacción.
2. Pruebas de idempotencia por firma/evento, transacción compartida entre ambos filtros, reanudación tras reinicio, paginación catch-up, `429`/`Retry-After`, backoff, pausas por cuota y límite de DB.
3. Prueba de SQLite temporal: esquema detectado como `tokens` y `trades` por `inspect_db`; `load_data` recupera filas y construye rutas de precio; el CSV vuelve a leer correctamente con encoding UTF-8.
4. Prueba de umbral: 1.000 eventos no lanza análisis; 1.001 sí produce snapshot y lanza una sola corrida; el servicio continúa capturando después.
5. El smoke test live, cuando se autorice la implementación y se configure una clave rotada, procesa una muestra real sin firma de transacciones. Se verifican firmas en Helius, persistencia, ausencia de duplicados y ausencia de secretos en logs. La prueba respeta los topes anteriores.
6. La base canónica y las bases sintéticas no cambian. Ninguna fila del CSV se clasifica como real sin firma/slot on-chain y origen registrado.

## Fuentes

1. [Helius — Stream Pump AMM Data](https://www.helius.dev/docs/rpc/websocket/stream-pump-amm-data): logs WebSocket para Pump AMM, recuperación de transacción y disponibilidad declarada en todos los planes.
2. [Helius — logsSubscribe](https://www.helius.dev/docs/api-reference/rpc/websocket/logssubscribe): endpoints, notificación de firmas/logs y recomendación de ping periódico.
3. [Helius — Pricing](https://www.helius.dev/pricing): límites publicados de Free y créditos de RPC.
4. [Solana — logsSubscribe](https://solana.com/docs/rpc/websocket/logssubscribe): filtro `mentions` de una dirección y formato de notificación.
5. [Solana — getSignaturesForAddress](https://solana.com/docs/rpc/http/getsignaturesforaddress): consulta de firmas y slots por dirección para recuperar intervalos.
6. [Solana — RPC clusters](https://solana.com/docs/references/clusters): límites de endpoints públicos y advertencia sobre uso productivo.
7. [Pump.fun — public docs/IDL](https://github.com/pump-fun/pump-public-docs): referencia oficial evolutiva para instrucciones y eventos de Pump.fun/PumpSwap.
8. [PumpPortal — Real-time Data](https://pumpportal.fun/data-api/real-time/) y [Fees](https://pumpportal.fun/fees/): tipos de stream y cargos de datos.
