# POST /pnl/coin/{coinId}/holders

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/pnl/coin/{coinId}/holders`
- **Method:** `POST`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `postPnlCoinByCoinIdHolders`
- **Summary:** POST /pnl/coin/{coinId}/holders

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `coinId` | `string` | path | ✓ |  |

## Responses

### 201

## Example Usage

### cURL

```bash
curl -X POST "https://advanced-api-v2.pump.fun/pnl/coin/<coinId>/holders" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
