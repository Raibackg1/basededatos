# GET /go/{goId}/submissions/{submissionId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/go/{goId}/submissions/{submissionId}`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getGoByGoIdSubmissionsBySubmissionId`
- **Summary:** GET /go/{goId}/submissions/{submissionId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `goId` | `string` | path | ✓ |  |
| `submissionId` | `string` | path | ✓ |  |
| `_rsc` | `string` | query | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/go/<goId>/submissions/<submissionId>?_rsc=<_rsc>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
