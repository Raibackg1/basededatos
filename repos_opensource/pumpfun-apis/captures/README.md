# Captures

Dated snapshots of pump.fun API endpoints, reverse-engineered from live browser
traffic (HAR captures run through [har2api](https://github.com/VTX-Labs/har2api)).

Each dated folder is a self-contained capture:

```
captures/<YYYY-MM-DD>/
  captured-api.json    OpenAPI 3.1 spec for the capture
  INDEX.md             grouped list of every endpoint in the capture
  endpoints/           one METHOD-path.md per operation
```

## Captures

| Date       | Endpoints | Spec                                              | Index                                  |
| ---------- | --------- | ------------------------------------------------- | -------------------------------------- |
| 2026-06-17 | 120       | [captured-api.json](./2026-06-17/captured-api.json) | [INDEX.md](./2026-06-17/INDEX.md)      |

## Adding a new capture

1. Record a HAR from pump.fun in browser DevTools (Network tab → Save all as HAR).
2. Convert it to OpenAPI:
   ```bash
   npx @vtx-labs/har2api pump.fun.har -f openapi-json -o captured-api.json \
     --host "advanced-api-v2.pump.fun,advanced-indexer.pump.fun,frontend-api-v3.pump.fun,livestream-api.pump.fun,profile-api.pump.fun,swap-api.pump.fun,pump.fun,pump-fe.helius-rpc.com"
   ```
3. Scrub it: remove every `example`/`examples`/`default` value, drop `enum`s and
   property-map keys that are inferred wallet addresses / session ids, and
   re-template any address or session literal that leaked into a path or string.
   The published spec must contain **no auth, wallet, session, or personal data** —
   only structural schema (paths, methods, param names, types).
4. Place it under `captures/<YYYY-MM-DD>/captured-api.json` with an `INDEX.md` and a
   per-endpoint `endpoints/` folder (see the existing capture for the format).
5. Add a row to the table above.
