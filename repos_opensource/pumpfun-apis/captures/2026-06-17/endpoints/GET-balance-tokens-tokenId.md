# GET /balance/tokens/{tokenId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/balance/tokens/{tokenId}`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getBalanceTokensByTokenId`
- **Summary:** GET /balance/tokens/{tokenId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `tokenId` | `string` | path | ✓ |  |
| `page` | `string` | query | ✓ |  |
| `size` | `string` | query | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/balance/tokens/<tokenId>?page=<page>&size=<size>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
