# GET /livestream/history

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/livestream/history`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getLivestreamHistory`
- **Summary:** GET /livestream/history

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `limit` | `string` | query | ✓ |  |
| `mintId` | `string` | query | ✓ |  |
| `offset` | `string` | query | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/livestream/history?limit=<limit>&mintId=<mintId>&offset=<offset>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
