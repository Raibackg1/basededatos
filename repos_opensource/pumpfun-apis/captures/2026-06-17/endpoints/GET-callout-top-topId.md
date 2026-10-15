# GET /callout/top/{topId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/callout/top/{topId}`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getCalloutTopByTopId`
- **Summary:** GET /callout/top/{topId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `topId` | `string` | path | ✓ |  |
| `limit` | `string` | query | ✓ |  |
| `sortBy` | `string` | query | ✓ |  |
| `sortOrder` | `string` | query | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/callout/top/<topId>?limit=<limit>&sortBy=<sortBy>&sortOrder=<sortOrder>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
