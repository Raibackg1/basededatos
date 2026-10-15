# OPTIONS /moderation/ban/address/{addresId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/moderation/ban/address/{addresId}`
- **Method:** `OPTIONS`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `optionsModerationBanAddressByAddresId`
- **Summary:** OPTIONS /moderation/ban/address/{addresId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `addresId` | `string` | path | ✓ |  |

## Responses

### 204

## Example Usage

### cURL

```bash
curl -X OPTIONS "https://advanced-api-v2.pump.fun/moderation/ban/address/<addresId>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
