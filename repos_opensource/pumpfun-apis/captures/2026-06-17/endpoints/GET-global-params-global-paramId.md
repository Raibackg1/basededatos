# GET /global-params/{global-paramId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/global-params/{global-paramId}`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getGlobal-paramsByGlobal-paramId`
- **Summary:** GET /global-params/{global-paramId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `global-paramId` | `string` | path | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/global-params/<global-paramId>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
