# GET /boards/movers

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/boards/movers`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getBoardsMovers`
- **Summary:** GET /boards/movers

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `limit` | `string` | query | ✓ |  |
| `session_id` | `string` | query | ✓ |  |
| `tier` | `string` | query | ✓ |  |
| `userId` | `string` | query | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/boards/movers?limit=<limit>&session_id=<session_id>&tier=<tier>&userId=<userId>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
