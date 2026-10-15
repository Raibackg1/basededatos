# GET /livestream/is-approved-creator

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/livestream/is-approved-creator`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getLivestreamIs-approved-creator`
- **Summary:** GET /livestream/is-approved-creator

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `mintId` | `string` | query | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/livestream/is-approved-creator?mintId=<mintId>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
