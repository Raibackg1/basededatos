# GET /go/action-items

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/go/action-items`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getGoAction-items`
- **Summary:** GET /go/action-items

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `_rsc` | `string` | query | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/go/action-items?_rsc=<_rsc>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
