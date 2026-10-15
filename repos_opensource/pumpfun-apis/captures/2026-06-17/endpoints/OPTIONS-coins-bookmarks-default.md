# OPTIONS /coins/bookmarks/default

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/coins/bookmarks/default`
- **Method:** `OPTIONS`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `optionsCoinsBookmarksDefault`
- **Summary:** OPTIONS /coins/bookmarks/default

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `includeNsfw` | `string` | query | ✓ |  |
| `limit` | `string` | query | ✓ |  |
| `offset` | `string` | query | ✓ |  |

## Responses

### 204

## Example Usage

### cURL

```bash
curl -X OPTIONS "https://advanced-api-v2.pump.fun/coins/bookmarks/default?includeNsfw=<includeNsfw>&limit=<limit>&offset=<offset>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
