# OPTIONS /callout/list/{listId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/callout/list/{listId}`
- **Method:** `OPTIONS`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `optionsCalloutListByListId`
- **Summary:** OPTIONS /callout/list/{listId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `listId` | `string` | path | ✓ |  |
| `limit` | `string` | query | ✓ |  |
| `sortBy` | `string` | query | ✓ |  |
| `sortOrder` | `string` | query | ✓ |  |

## Responses

### 204

## Example Usage

### cURL

```bash
curl -X OPTIONS "https://advanced-api-v2.pump.fun/callout/list/<listId>?limit=<limit>&sortBy=<sortBy>&sortOrder=<sortOrder>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
