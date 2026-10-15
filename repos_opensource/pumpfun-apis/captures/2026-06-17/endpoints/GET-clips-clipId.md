# GET /clips/{clipId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/clips/{clipId}`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getClipsByClipId`
- **Summary:** GET /clips/{clipId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `clipId` | `string` | path | ✓ |  |
| `clipType` | `string` | query | ✓ |  |
| `limit` | `string` | query | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/clips/<clipId>?clipType=<clipType>&limit=<limit>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
