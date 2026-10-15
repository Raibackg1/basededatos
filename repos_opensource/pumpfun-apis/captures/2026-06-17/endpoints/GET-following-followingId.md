# GET /following/{followingId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/following/{followingId}`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getFollowingByFollowingId`
- **Summary:** GET /following/{followingId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `followingId` | `string` | path | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/following/<followingId>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
