# GET /bounties/v2/tasks

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/bounties/v2/tasks`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getBountiesV2Tasks`
- **Summary:** GET /bounties/v2/tasks

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `coinAddress` | `string` | query |  |  |
| `creatorAddress` | `string` | query |  |  |
| `facets` | `string` | query |  |  |
| `includeBanned` | `string` | query |  |  |
| `limit` | `string` | query | ✓ |  |
| `order` | `string` | query |  |  |
| `phase` | `string` | query |  |  |
| `sort` | `string` | query |  |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/bounties/v2/tasks?coinAddress=<coinAddress>&creatorAddress=<creatorAddress>&facets=<facets>&includeBanned=<includeBanned>&limit=<limit>&order=<order>&phase=<phase>&sort=<sort>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
