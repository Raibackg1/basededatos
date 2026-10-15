# OPTIONS /v1/coins/{coinId}/trades/batch

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/v1/coins/{coinId}/trades/batch`
- **Method:** `OPTIONS`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `optionsV1CoinsByCoinIdTradesBatch`
- **Summary:** OPTIONS /v1/coins/{coinId}/trades/batch

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `coinId` | `string` | path | ✓ |  |

## Responses

### 204

## Example Usage

### cURL

```bash
curl -X OPTIONS "https://advanced-api-v2.pump.fun/v1/coins/<coinId>/trades/batch" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
