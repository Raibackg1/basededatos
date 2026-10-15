# GET /bounties/v2/top-earners

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/bounties/v2/top-earners`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getBountiesV2Top-earners`
- **Summary:** GET /bounties/v2/top-earners

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `limit` | `string` | query | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/bounties/v2/top-earners?limit=<limit>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
