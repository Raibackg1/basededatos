# GET /v1/coins/{coinId}/market-activity

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/v1/coins/{coinId}/market-activity`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getV1CoinsByCoinIdMarket-activity`
- **Summary:** GET /v1/coins/{coinId}/market-activity

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `coinId` | `string` | path | ✓ |  |
| `chainId` | `string` | query | ✓ |  |
| `program` | `string` | query | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/v1/coins/<coinId>/market-activity?chainId=<chainId>&program=<program>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
