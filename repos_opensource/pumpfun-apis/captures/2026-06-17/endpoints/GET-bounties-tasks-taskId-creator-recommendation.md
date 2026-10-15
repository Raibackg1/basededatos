# GET /bounties/tasks/{taskId}/creator-recommendation

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/bounties/tasks/{taskId}/creator-recommendation`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getBountiesTasksByTaskIdCreator-recommendation`
- **Summary:** GET /bounties/tasks/{taskId}/creator-recommendation

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `taskId` | `string` | path | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/bounties/tasks/<taskId>/creator-recommendation" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
