# OPTIONS /callout/top/{topId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/callout/top/{topId}`
- **Method:** `OPTIONS`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `optionsCalloutTopByTopId`
- **Summary:** OPTIONS /callout/top/{topId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `topId` | `string` | path | ✓ |  |
| `limit` | `string` | query | ✓ |  |
| `sortBy` | `string` | query | ✓ |  |
| `sortOrder` | `string` | query | ✓ |  |

## Responses

### 204

## Example Usage

### cURL

```bash
curl -X OPTIONS "https://advanced-api-v2.pump.fun/callout/top/<topId>?limit=<limit>&sortBy=<sortBy>&sortOrder=<sortOrder>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
