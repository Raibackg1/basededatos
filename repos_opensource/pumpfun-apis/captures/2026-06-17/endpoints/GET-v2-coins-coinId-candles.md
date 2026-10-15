# GET /v2/coins/{coinId}/candles

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/v2/coins/{coinId}/candles`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getV2CoinsByCoinIdCandles`
- **Summary:** GET /v2/coins/{coinId}/candles

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `coinId` | `string` | path | ✓ |  |
| `beforeTs` | `string` | query |  |  |
| `chainId` | `string` | query |  |  |
| `createdTs` | `string` | query | ✓ |  |
| `currency` | `string` | query |  |  |
| `interval` | `string` | query | ✓ |  |
| `limit` | `string` | query | ✓ |  |
| `program` | `string` | query |  |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/v2/coins/<coinId>/candles?beforeTs=<beforeTs>&chainId=<chainId>&createdTs=<createdTs>&currency=<currency>&interval=<interval>&limit=<limit>&program=<program>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
