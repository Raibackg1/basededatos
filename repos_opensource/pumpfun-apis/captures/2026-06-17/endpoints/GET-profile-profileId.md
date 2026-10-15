# GET /profile/{profileId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/profile/{profileId}`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getProfileByProfileId`
- **Summary:** GET /profile/{profileId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `profileId` | `string` | path | ✓ |  |
| `_rsc` | `string` | query | ✓ |  |
| `tab` | `string` | query |  |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/profile/<profileId>?_rsc=<_rsc>&tab=<tab>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
