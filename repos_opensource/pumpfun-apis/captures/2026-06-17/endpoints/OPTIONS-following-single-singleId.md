# OPTIONS /following/single/{singleId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/following/single/{singleId}`
- **Method:** `OPTIONS`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `optionsFollowingSingleBySingleId`
- **Summary:** OPTIONS /following/single/{singleId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `singleId` | `string` | path | ✓ |  |
| `userId` | `string` | query | ✓ |  |

## Responses

### 204

## Example Usage

### cURL

```bash
curl -X OPTIONS "https://advanced-api-v2.pump.fun/following/single/<singleId>?userId=<userId>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
