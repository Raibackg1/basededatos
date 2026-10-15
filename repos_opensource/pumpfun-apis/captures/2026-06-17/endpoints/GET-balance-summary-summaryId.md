# GET /balance/summary/{summaryId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/balance/summary/{summaryId}`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getBalanceSummaryBySummaryId`
- **Summary:** GET /balance/summary/{summaryId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `summaryId` | `string` | path | ✓ |  |

## Responses

### 200

### 429

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/balance/summary/<summaryId>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
