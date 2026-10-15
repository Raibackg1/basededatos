# GET /v3/followers/count/{countId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/v3/followers/count/{countId}`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getV3FollowersCountByCountId`
- **Summary:** GET /v3/followers/count/{countId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `countId` | `string` | path | ✓ |  |

## Responses

### 404

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/v3/followers/count/<countId>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
