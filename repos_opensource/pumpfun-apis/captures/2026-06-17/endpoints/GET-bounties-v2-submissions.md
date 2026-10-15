# GET /bounties/v2/submissions

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/bounties/v2/submissions`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getBountiesV2Submissions`
- **Summary:** GET /bounties/v2/submissions

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `limit` | `string` | query | ✓ |  |
| `requesterAddress` | `string` | query | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/bounties/v2/submissions?limit=<limit>&requesterAddress=<requesterAddress>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
