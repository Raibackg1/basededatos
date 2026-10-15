# GET /moderation/ban/address/{addresId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/moderation/ban/address/{addresId}`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getModerationBanAddressByAddresId`
- **Summary:** GET /moderation/ban/address/{addresId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `addresId` | `string` | path | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/moderation/ban/address/<addresId>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
