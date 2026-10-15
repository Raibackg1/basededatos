# GET /v2/coins/{coinId}/trades

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/v2/coins/{coinId}/trades`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getV2CoinsByCoinIdTrades`
- **Summary:** GET /v2/coins/{coinId}/trades

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `coinId` | `string` | path | ✓ |  |
| `chainId` | `string` | query | ✓ |  |
| `createdTs` | `string` | query |  |  |
| `cursor` | `string` | query | ✓ |  |
| `limit` | `string` | query | ✓ |  |
| `minSolAmount` | `string` | query |  |  |
| `program` | `string` | query | ✓ |  |
| `userAddress` | `string` | query |  |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/v2/coins/<coinId>/trades?chainId=<chainId>&createdTs=<createdTs>&cursor=<cursor>&limit=<limit>&minSolAmount=<minSolAmount>&program=<program>&userAddress=<userAddress>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
