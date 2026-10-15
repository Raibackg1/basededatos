# GET /coins/search-unrestricted

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/coins/search-unrestricted`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getCoinsSearch-unrestricted`
- **Summary:** GET /coins/search-unrestricted

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `currentlyLive` | `string` | query |  |  |
| `includeNsfw` | `string` | query | ✓ |  |
| `isCharity` | `string` | query |  |  |
| `limit` | `string` | query | ✓ |  |
| `offset` | `string` | query | ✓ |  |
| `order` | `string` | query |  |  |
| `sort` | `string` | query | ✓ |  |
| `tokenizedAgent` | `string` | query |  |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/coins/search-unrestricted?currentlyLive=<currentlyLive>&includeNsfw=<includeNsfw>&isCharity=<isCharity>&limit=<limit>&offset=<offset>&order=<order>&sort=<sort>&tokenizedAgent=<tokenizedAgent>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
