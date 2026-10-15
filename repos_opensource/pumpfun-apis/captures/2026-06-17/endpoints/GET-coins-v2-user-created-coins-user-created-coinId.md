# GET /coins-v2/user-created-coins/{user-created-coinId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/coins-v2/user-created-coins/{user-created-coinId}`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getCoins-v2User-created-coinsByUser-created-coinId`
- **Summary:** GET /coins-v2/user-created-coins/{user-created-coinId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `user-created-coinId` | `string` | path | ✓ |  |
| `limit` | `string` | query | ✓ |  |
| `offset` | `string` | query | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/coins-v2/user-created-coins/<user-created-coinId>?limit=<limit>&offset=<offset>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
