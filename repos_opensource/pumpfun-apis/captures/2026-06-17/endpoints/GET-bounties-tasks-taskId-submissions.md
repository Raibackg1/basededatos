# GET /bounties/tasks/{taskId}/submissions

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/bounties/tasks/{taskId}/submissions`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getBountiesTasksByTaskIdSubmissions`
- **Summary:** GET /bounties/tasks/{taskId}/submissions

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `taskId` | `string` | path | ✓ |  |
| `limit` | `string` | query | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/bounties/tasks/<taskId>/submissions?limit=<limit>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
