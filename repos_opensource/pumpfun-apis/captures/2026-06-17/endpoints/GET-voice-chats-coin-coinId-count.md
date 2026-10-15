# GET /voice-chats/coin/{coinId}/count

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/voice-chats/coin/{coinId}/count`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getVoice-chatsCoinByCoinIdCount`
- **Summary:** GET /voice-chats/coin/{coinId}/count

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `coinId` | `string` | path | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/voice-chats/coin/<coinId>/count" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
