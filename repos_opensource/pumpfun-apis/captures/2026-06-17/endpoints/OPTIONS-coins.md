# OPTIONS /coins

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/coins`
- **Method:** `OPTIONS`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `optionsCoins`
- **Summary:** OPTIONS /coins

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `includeNsfw` | `string` | query | ✓ |  |
| `limit` | `string` | query | ✓ |  |
| `offset` | `string` | query | ✓ |  |
| `order` | `string` | query | ✓ |  |
| `sort` | `string` | query | ✓ |  |

## Responses

### 204

## Example Usage

### cURL

```bash
curl -X OPTIONS "https://advanced-api-v2.pump.fun/coins?includeNsfw=<includeNsfw>&limit=<limit>&offset=<offset>&order=<order>&sort=<sort>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
