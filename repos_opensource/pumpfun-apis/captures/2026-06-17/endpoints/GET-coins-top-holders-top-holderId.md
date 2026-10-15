# GET /coins/top-holders/{top-holderId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/coins/top-holders/{top-holderId}`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getCoinsTop-holdersByTop-holderId`
- **Summary:** GET /coins/top-holders/{top-holderId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `top-holderId` | `string` | path | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/coins/top-holders/<top-holderId>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
