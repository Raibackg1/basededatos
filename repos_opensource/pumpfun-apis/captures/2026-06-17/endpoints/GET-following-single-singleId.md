# GET /following/single/{singleId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/following/single/{singleId}`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getFollowingSingleBySingleId`
- **Summary:** GET /following/single/{singleId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `singleId` | `string` | path | ✓ |  |
| `userId` | `string` | query | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/following/single/<singleId>?userId=<userId>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
