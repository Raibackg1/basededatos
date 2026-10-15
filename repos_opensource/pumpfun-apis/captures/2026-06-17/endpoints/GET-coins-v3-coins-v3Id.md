# GET /coins-v3/{coins-v3Id}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/coins-v3/{coins-v3Id}`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getCoins-v3ByCoins-v3Id`
- **Summary:** GET /coins-v3/{coins-v3Id}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `coins-v3Id` | `string` | path | ✓ |  |
| `includeLiveStreamInfo` | `string` | query |  |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/coins-v3/<coins-v3Id>?includeLiveStreamInfo=<includeLiveStreamInfo>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
