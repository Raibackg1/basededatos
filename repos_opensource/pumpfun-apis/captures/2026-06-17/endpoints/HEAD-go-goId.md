# HEAD /go/{goId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/go/{goId}`
- **Method:** `HEAD`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `headGoByGoId`
- **Summary:** HEAD /go/{goId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `goId` | `string` | path | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X HEAD "https://advanced-api-v2.pump.fun/go/<goId>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
