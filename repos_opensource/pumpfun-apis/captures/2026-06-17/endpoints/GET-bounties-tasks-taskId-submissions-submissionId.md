# GET /bounties/tasks/{taskId}/submissions/{submissionId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/bounties/tasks/{taskId}/submissions/{submissionId}`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getBountiesTasksByTaskIdSubmissionsBySubmissionId`
- **Summary:** GET /bounties/tasks/{taskId}/submissions/{submissionId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `taskId` | `string` | path | ✓ |  |
| `submissionId` | `string` | path | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/bounties/tasks/<taskId>/submissions/<submissionId>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
